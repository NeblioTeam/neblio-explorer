#!/usr/bin/env python3

import argparse
import jsmin
import simplejson as json
import logging
import logging.handlers
import os
import pymongo
import shutil
import subprocess
import time
import pprint
import urllib.request, json

from bitcoinrpc.authproxy import AuthServiceProxy
from configobj import ConfigObj


parser = argparse.ArgumentParser(description='explorer sync parameters')
parser.add_argument('--explorer-config', dest='explorer_config', type=str,
                    help='explorer config file', required=True)
parser.add_argument('--log-level', dest='loglevel', type=str, default="INFO",
                    help='set log level',
                    choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"])
parser.add_argument('--log-file', dest='logfile', type=str,
                    help='log file location')


NUM_UNITS = 100000000

ntp1_api_url = ''

class DecimalEncoder(json.JSONEncoder):
    def _iterencode(self, o, markers=None):
        if isinstance(o, decimal.Decimal):
            # wanted a simple yield str(o) in the next line,
            # but that would mean a yield on the line with super(...),
            # which wouldn't work (see my comment below), so...
            return (str(o) for o in [o])
        return super(DecimalEncoder, self)._iterencode(o, markers)


class ReorgException(Exception):
    pass


def get_explorer_config(cfg_file):
    if os.path.isfile(cfg_file) is False:
        raise IOError("Config file %s not found" % cfg_file)

    with open(cfg_file, 'r') as fp:
        return json.loads(jsmin.jsmin(fp.read()))


class Database(object):

    def __init__(self, cfg, coin):
        self._coin = coin
        db_cfg = self._validate_db_cfg(cfg["dbsettings"])
        self._db_uri = 'mongodb://%s:%s@%s:%s/%s' % (
            db_cfg[0], db_cfg[1], db_cfg[2], db_cfg[3], db_cfg[4])
        self._db_conn = pymongo.MongoClient(self._db_uri)
        self.db = self._db_conn[db_cfg[4]]
        self._ensure_collections_and_indexes()
        self._txcount = cfg.get("txcount", 200)
        global ntp1_api_url
        ntp1_api_url = cfg.get("ntp1api").get("url")

    def _validate_db_cfg(self, cfg):
        database = cfg.get("database")
        user = cfg.get("user")
        password = cfg.get("password")
        addr = cfg.get("address")
        port = cfg.get("port")
        if None in (database, user, password, addr, port):
            raise ValueError("Invalid mongo config")
        return (user, password, addr, port, database)



    def get_stats(self):
        stats = self.db.coinstats.find_one({"coin": self._coin})
        if stats is None:
            return None
        return stats

    def get_last_recorded_block(self):
        count = self.db.blocks.find().count()
        if count == 0:
            return None
        record = self.db.blocks.find().sort(
            [("height", pymongo.DESCENDING)]).limit(1)
        return record[0]

    def _process_vout(self, vouts, txid, addrs):
        if type(addrs) is not dict:
            raise ValueError("Invalid addrs dics")
        for out in vouts:
            address = out["addresses"]
            amount = out["amount"]
            if addrs.get(address) is None:
                tokens = []
                for out_token in out.get("tokens", []):
                    tokens.append({
                        "id": out_token["id"],
                        "received": int(out_token.get("amount", "0")),
                        "sent": 0,
                        "meta": out_token.get("meta", {})
                    })
                addrs[address] = {
                    "received": amount,
                    "sent": 0,
                    "tokens": tokens,
                    "txs": [
                        {
                            "type": "vout",
                            "addresses": txid
                        }
                    ]
                }
            else:
                details = addrs[address]
                details["received"] += amount
                details["txs"].append({
                    "type": "vout",
                    "addresses": txid
                })
                # process tokens
                for out_token in out.get("tokens", []):
                    token_exists = False
                    for addr_token in details["tokens"]:
                        if addr_token["id"] == out_token["id"]:
                            addr_token["received"] = addr_token.get("received", 0) + int(out_token.get("amount", "0"))
                            token_exists = True
                            #break
                    if not token_exists:
                        details["tokens"].append({
                            "id": out_token["id"],
                            "received": int(out_token.get("amount", "0")),
                            "meta": out_token.get("meta", {})
                        })
                addrs[address] = details
        return addrs

    def _process_vin(self, vins, txid, addrs):
        if type(addrs) is not dict:
            raise ValueError("Invalid addrs dics")
        for vin in vins:
            address = vin["addresses"]
            amount = vin["amount"]
            if addrs.get(address) is None:
                tokens = []
                for vin_token in vin.get("tokens", []):
                    tokens.append({
                        "id": vin_token["id"],
                        "sent": int(vin_token.get("amount", "0")),
                        "received": 0,
                        "meta": vin_token.get("meta", {})
                    })
                addrs[address] = {
                    "sent": amount,
                    "received": 0,
                    "tokens": tokens,
                    "txs": [
                        {
                            "type": "vin",
                            "addresses": txid
                        }
                    ]
                }
            else:
                details = addrs[address]
                details["sent"] += amount
                details["txs"].append({
                    "type": "vin",
                    "addresses": txid
                })
                # process tokens
                for vin_token in vin.get("tokens", []):
                    token_exists = False
                    for addr_token in details["tokens"]:
                        if addr_token["id"] == vin_token["id"]:
                            addr_token["sent"] = addr_token.get("sent", 0) + int(vin_token.get("amount", "0"))
                            token_exists = True
                            #break
                    if not token_exists:
                        details["tokens"].append({
                            "id": vin_token["id"],
                            "sent": int(vin_token.get("amount", "0")),
                            "meta": vin_token.get("meta", {})
                        })
                addrs[address] = details
        return addrs

    def get_address_info(self, address):
        addr = self.db.addresses.find_one({"a_id": address})
        if addr is None:
            raise ValueError("No such address %s" % address)
        return addr

    def _prepare_ins_outs(self, transactions):
        addrs = {}
        for tx in transactions:
            vout = tx["vout"]
            vin = tx["vin"]
            addrs = self._process_vout(vout, tx["txid"], addrs)
            addrs = self._process_vin(vin, tx["txid"], addrs)
        return addrs

    def update_token(self, token_id, retries=0):
        if retries > 10: return
        try:
            data1 = urllib.request.urlopen(ntp1_api_url + 'tokenmetadata/' + token_id).read()
            metadata = json.loads(data1)
            someUtxo = metadata.get("someUtxo", "")
            #logger.info("Getting metdata for token: "+token_id)
        except Exception as err:
            logger.warning("RETRY: Error getting initial metadata: %s" % err)
            time.sleep(10)
            retries += 1
            self.update_token(token_id, retries)
        if (someUtxo):
            try:
                #logger.info("At UTXO: "+someUtxo)
                data2 = urllib.request.urlopen(ntp1_api_url + 'tokenmetadata/' + token_id + '/' + someUtxo).read()
                metadata = json.loads(data2)
            except Exception as err:
                logger.warning("RETRY: Error getting extended metadata: %s" % err)
                time.sleep(10)
                retries += 1
                self.update_token(token_id, retries)
            # check for a firstBlock of -1
            if (metadata.get("firstBlock", 0) < 0):
                logger.warning("RETRY: Invalid first block in token metadata: %s" % err)
                time.sleep(10)
                retries += 1
                self.update_token(token_id, retries)
            # successfully got all metadata, insert/update it in the db
            token = self.db.tokens.find_one({"t_id": token_id})
            if token is None:
                logger.info("Adding new token to the db: "+token_id)
                self.db.tokens.insert_one(
                    {
                        "t_id": token_id,
                        "meta_of_issuance": metadata.get("metadataOfIssuence", {}),
                        "issuance_address": metadata.get("issueAddress", ""),
                        "issuance_txid": metadata.get("issuanceTxid", ""),
                        "first_block": metadata.get("firstBlock", 0),
                        "num_burns": metadata.get("numOfBurns", 0),
                        "num_issuance": metadata.get("numOfIssuance", 0),
                        "num_transfers": metadata.get("numOfTransfers", 0),
                        "num_holders": metadata.get("numOfHolders", 0),
                        "total_supply": metadata.get("totalSupply", 0),
                        "aggregation_policy": metadata.get("aggregationPolicy", ""),
                        "lock_status": metadata.get("lockStatus", ""),
                        "divisibility": metadata.get("divisibility", 0)
                    }
                )
            else:
                #logger.info("Updating token stats in the db: "+token_id)
                self.db.tokens.update_one(
                    {"t_id": token_id},
                    {
                        "$set": {
                            "first_block": metadata.get("firstBlock", 0),
                            "num_burns": metadata.get("numOfBurns", 0),
                            "num_issuance": metadata.get("numOfIssuance", 0),
                            "num_transfers": metadata.get("numOfTransfers", 0),
                            "num_holders": metadata.get("numOfHolders", 0),
                            "total_supply": metadata.get("totalSupply", 0)
                        }
                    }
                )
        else:
            logger.warning("RETRY: No UTXO, cannot update token in db for: "+token_id)
            time.sleep(10)
            retries += 1
            self.update_token(token_id, retries)

    def update_addresses(self, transactions):
        addrs = self._prepare_ins_outs(transactions)
        for addr in addrs:
            info = self.db.addresses.find_one({"a_id": addr})
            if info:
                sent = info.get("sent", 0) + addrs[addr].get("sent", 0)
                received = info.get("received", 0) + addrs[addr].get("received", 0)
                txs = info.get("txs", [])
                for tx in addrs[addr].get("txs", []):
                    already_exists = False
                    for txi in txs:
                        if txi["addresses"] == tx["addresses"]:
                            already_exists = True
                            break
                    if already_exists:
                        continue
                    txs.append(tx)
                balance = received - sent
                addr_tokens = info.get("tokens", [])
                for tx_token in addrs[addr].get("tokens", []):
                    token_exists = False
                    for addr_token in addr_tokens:
                        if tx_token["id"] == addr_token["id"]:
                            addr_token["sent"] =  addr_token.get("sent", 0) + tx_token.get("sent", 0)
                            addr_token["received"] = addr_token.get("received", 0) + tx_token.get("received", 0)
                            addr_token["amount"] = addr_token["received"] - addr_token["sent"]
                            self.update_token(tx_token["id"]);
                            token_exists = True
                            break
                    if not token_exists:
                        addr_token = {}
                        addr_token["id"] = tx_token["id"]
                        addr_token["sent"] = tx_token.get("sent", 0)
                        addr_token["received"] = tx_token.get("received", 0)
                        addr_token["amount"] = addr_token["received"] - addr_token["sent"]
                        addr_token["meta"] = tx_token["meta"]
                        addr_tokens.append(addr_token)
                        self.update_token(tx_token["id"]);

                self.db.addresses.update_one(
                    {"a_id": addr},
                    {
                        "$set": {
                            "sent": sent,
                            "received": received,
                            "balance": balance,
                            "tokens": addr_tokens,
                            "txs": txs[-self._txcount:],
                        }
                    })
            else:
                addr_tokens = []
                for tx_token in addrs[addr].get("tokens", []):
                    addr_token = {}
                    addr_token["id"] = tx_token["id"]
                    addr_token["sent"] = tx_token.get("sent", 0)
                    addr_token["received"] = tx_token.get("received", 0)
                    addr_token["amount"] = addr_token["received"] - addr_token["sent"]
                    addr_token["meta"] = tx_token["meta"]
                    addr_tokens.append(addr_token)
                    self.update_token(tx_token["id"]);
                sent = addrs[addr].get("sent", 0)
                received = addrs[addr].get("received", 0)
                txs = addrs[addr].get("txs", [])
                balance = received - sent
                self.db.addresses.insert_one(
                    {
                        "a_id": addr,
                        "sent": sent,
                        "received": received,
                        "balance": balance,
                        "tokens": addr_tokens,
                        "txs": txs[-self._txcount:],
                    }
                )
        return len(addrs)

    def rollback_addresses(self, transactions):
        if type(transactions) is not list:
            raise ValueError("transactions object must be list")
        addrs = self._prepare_ins_outs(transactions)
        for addr in addrs:
            info = self.db.addresses.find_one({"a_id": addr})
            if info is None:
                continue
            sent = info.get("sent", 0) - addrs[addr].get("sent", 0)
            received = info.get("received", 0) - addrs[addr].get("received", 0)
            # rollback token amounts
            db_tokens = info.get("tokens", [])
            addr_tokens = addrs[addr].get("tokens", [])
            for db_token in db_tokens:
                for addr_token in addr_tokens:
                    if (addr_token["id"] == db_token["id"]):
                        db_token_sent = db_token.get("sent", 0) - addr_token.get("sent", 0)
                        db_token_received = db_token.get("recevied", 0) - addr_token.get("received", 0)
                        db_token["sent"] = db_token_sent
                        db_token["received"] = db_token_received
                        db_token["amount"] = db_token["received"] - db_token["sent"]
            for tx in addrs[addr].get("txs", []):
                if tx in info.get("txs", []):
                    info["txs"].remove(tx)
            balance = received - sent
            self.db.addresses.update_one(
                {"a_id": addr},
                {
                    "$set": {
                        "sent": sent,
                        "received": received,
                        "balance": balance,
                        "tokens": db_tokens,
                        "txs": info["txs"],
                    }
                })

    def rollback(self, blockhash):
        """Rollback changes made for a particular blockhash"""
        txs = self.db.txes.find({"blockhash": blockhash})
        transactions = []
        if txs:
            for i in txs:
                transactions.append(i)
        self.rollback_addresses(transactions)
        self.db.txes.delete_many({"blockhash": blockhash})
        self.db.blocks.delete_many({"hash": blockhash})

    def update_transactions(self, transactions):
        self.db.txes.insert_many(transactions)

    def update_richlist(self):
        balance = list(self.db.addresses.find().sort(
            [("balance", pymongo.DESCENDING)]).limit(102))
        received = list(self.db.addresses.find().sort(
            [("received", pymongo.DESCENDING)]).limit(101))
        self.db.richlists.update_one(
            {"coin": self._coin},
            {
                "$set": {
                    "balance": balance,
                    "received": received,
                }
            }, upsert=True)

    def update_stats(self, stats):
        if type(stats) is not dict:
            raise ValueError("Invalid stats object")
        supply = stats.get("supply")
        lastHeight = stats.get("block")
        count = stats.get("count")
        if None in (supply, lastHeight, count):
            # Invalid stats given. Should log something here
            raise ValueError("Invalid stats object")
        self.db.coinstats.find_one_and_update(
            {"coin": self._coin},
            {
                "$set": {
                    "supply": supply,
                    "last": lastHeight,
                    "count": count,
                 }
            })

    def _ensure_collections_and_indexes(self):
        names = self.db.list_collection_names()
        if "blocks" not in names:
            self.db.create_collection("blocks")
            self.db.blocks.create_index("height", unique=True)
            self.db.blocks.create_index("hash")
        if "txes" in names:
            self.db.txes.create_index("blockhash")
            self.db.txes.create_index("blockindex")
            self.db.txes.create_index("timestamp")
            self.db.txes.create_index("txid")
        if "addresses" in names:
            self.db.addresses.create_index("a_id")
            self.db.addresses.create_index("received")
            self.db.addresses.create_index("balance")
        if "tokens" in names:
            self.db.tokens.create_index("t_id")
        if "peers" in names:
            self.db.peers.create_index("createdAt",expireAfterSeconds=86400)


class TxIn(object):

    def __init__(self, txin, version):
        self._in = txin
        self._vers = version

    def is_valid(self):
        script = self._in.get("scriptSig", {})
        asm = script.get("asm")
        if asm:
            if asm.startswith("OP_RETURN"):
                return False
        return True

    def is_coinbase(self):
        return self._in.get("coinbase", False) is not False

    def input(self):
        if self.is_coinbase():
            return None
        return {"vout": self._in["vout"], "txid": self._in["txid"]}


class Tx(object):

    def __init__(self, tx, cli, height, timestamp):
        self._tx = tx
        self._cli = cli
        self._height = height
        self._vin = None
        self._vout = None
        self._time = timestamp

    def tx_id(self):
        return self._tx["txid"]

    def _get_metadata_of_issuance(self, token_id, retries=0):
        if retries > 10: return {}
        try:
            data1 = urllib.request.urlopen(ntp1_api_url + 'tokenmetadata/' + token_id).read()
            metadata = json.loads(data1)
            someUtxo = metadata.get("someUtxo", "")
            #logger.info("Getting metdata for token: "+token_id)
        except Exception as err:
            logger.warning("RETRY: Error getting initial metadata: %s" % err)
            time.sleep(10)
            retries += 1
            self._get_metadata_of_issuance(token_id, retries)
        if (someUtxo):
            try:
                #logger.info("At UTXO: "+someUtxo)
                data2 = urllib.request.urlopen(ntp1_api_url + 'tokenmetadata/' + token_id + '/' + someUtxo).read()
                metadata = json.loads(data2)
            except Exception as err:
                logger.warning("RETRY: Error getting extended metadata: %s" % err)
                time.sleep(10)
                retries += 1
                self._get_metadata_of_issuance(token_id, retries)
            # check for a firstBlock of -1
            if (metadata.get("firstBlock", 0) < 0):
                logger.warning("RETRY: Invalid first block in token metadata: %s" % err)
                time.sleep(10)
                retries += 1
                self._get_metadata_of_issuance(token_id, retries)
            return metadata
        else:
            logger.warning("RETRY:  No UTXO, cannot get token info for: "+token_id) %s" % err)
            time.sleep(10)
            retries += 1
            self._get_metadata_of_issuance(token_id, retries)

    def _output_is_valid(self, out):
        script = out.get("scriptPubKey", None)
        if script is None:
            return False
        asm = script.get("asm")
        if asm is None or asm.startswith("OP_RETURN"):
            return False
        return True

    def _get_input_details(self, vinInfo):
        vin = self._cli.get_transaction(vinInfo["txid"])
        voutIdx = vinInfo.get("vout")
        vouts = vin.get("vout")
        if vouts is None:
            return
        for i in vouts:
            n = i.get("n")
            if n is not None and int(n) == int(vinInfo["vout"]):
                scrypt = i.get("scriptPubKey")
                if scrypt is None:
                    return
                addr = scrypt.get("addresses")
                if addr is None:
                    addr = ["no address could be decoded",]
                # explorer expects id, not tokenId
                tokens = i.get("tokens", [])
                for t in tokens:
                    t["id"] = t.pop("tokenId")
                return {
                    "addresses": addr[0],
                    "amount": int(i["value"] * NUM_UNITS),
                    "tokens": tokens,
                }
        return

    def _get_coinbase_vin(self):
        vout = self._tx.get("vout")
        total = sum([int(i["value"] * NUM_UNITS) for i in vout])
        return {
            "addresses": "coinbase",
            "amount": total,
            "tokens": [],
        }

    def _is_coinbase(self):
        vin = self._tx["vin"]
        tx = TxIn(vin[0], self._tx["version"])
        return tx.is_coinbase()

    def inputs(self):
        if self._vin:
            return self._vin
        addr_map = {}
        for i in self._tx.get("vin"):
            tx = TxIn(i, self._tx["version"])
            if tx.is_coinbase():
                details = self._get_coinbase_vin()
                return [details,]
            if tx.is_valid() is False:
                continue

            details = self._get_input_details(tx.input())
            for t in details["tokens"]:
                tx_meta = self._get_metadata_of_issuance(t["id"])
                tx_meta_of_iss = tx_meta.get("metadataOfIssuence", {}).get("data", {})
                t["meta"] = tx_meta_of_iss
            if addr_map.get(details["addresses"]) is None:
                addr_map[details["addresses"]] = {}
                addr_map[details["addresses"]]["amount"] = details["amount"]
                addr_map[details["addresses"]]["tokens"] = details["tokens"]
            else:
                addr_map[details["addresses"]]["amount"] += details["amount"]
                addr_map[details["addresses"]]["tokens"].extend(details["tokens"])

        ret = [{"addresses": x, "amount": addr_map[x]["amount"], "tokens": addr_map[x]["tokens"]} for x in addr_map]
        self._vin = ret
        return ret

    def outputs(self):
        if self._vout:
            return self._vout
        ret = []
        vout = self._tx.get("vout", [])
        if len(vout) == 0:
            return ret
        tx = vout[0]

        is_nonstandard = tx["scriptPubKey"]["type"] == "nonstandard"
        is_stake = False

        if is_nonstandard:
            vout.pop(0)
            if len(vout) == 0:
                return ret
            addr = vout[0]["scriptPubKey"].get("addresses", [""])[0]
            vin = self.inputs()
            if len(vin):
                is_stake = addr == vin[0]["addresses"]

        addrs = {}

        for i in vout:
            if self._output_is_valid(i) is False:
                continue
            script = i.get("scriptPubKey", {})
            addresses = script.get("addresses")
            if addresses is None:
                addr = "no address could be decoded"
            else:
                addr = addresses[0]

            vout_tokens = i.get("tokens", [])
            for t in vout_tokens:
                # explorer expects id, not tokenId
                t["id"] = t.pop("tokenId")
                tx_meta = self._get_metadata_of_issuance(t["id"])
                tx_meta_of_iss = tx_meta.get("metadataOfIssuence", {}).get("data", {})
                t["meta"] = tx_meta_of_iss
            if addrs.get(addr):
                addrs[addr]["amount"] += int(i["value"] * NUM_UNITS)
                addrs[addr]["tokens"].extend(vout_tokens)
            else:
                addrs[addr] = {}
                addrs[addr]["amount"] = int(i["value"] * NUM_UNITS)
                addrs[addr]["tokens"] = vout_tokens

        has_token_inputs = False
        for inp in self.inputs():
            if(inp.get("tokens", [])):
                has_token_inputs = True
                break

        for addr in addrs:
            if is_stake and not has_token_inputs:
                val = addrs[addr]["amount"] - vin[0]["amount"]
            else:
                val = addrs[addr]["amount"]
            ret.append(
                {
                    "amount": val,
                    "addresses": addr,
                    "tokens": addrs[addr]["tokens"],
                    #"type": script.get("type"),
                    "is_stake": is_stake,
                }
            )
        self._vout = ret
        return ret

    def _get_total(self, vin, vout, is_coinbase):
        voutTotal = sum([i["amount"] for i in vout])
        if is_coinbase:
            vinAmount = vin[0]["amount"]
            if vinAmount != voutTotal:
                raise ValueError("Coinbase amount != vout Amount")
            return vinAmount

        vinTotal = sum([i["amount"] for i in vin])

        fee = (vinTotal - voutTotal)
        amount = vinTotal - fee
        return amount

    def details(self):
        is_coinbase = self._is_coinbase()
        outs = self.outputs()
        ins = self.inputs()
        total = self._get_total(ins, outs, is_coinbase)
        has_token_inputs = False
        for inp in ins:
            if(inp.get("tokens", [])):
                has_token_inputs = True
                break
        if len(outs) and outs[0]["is_stake"] and not has_token_inputs:
            ins = []

        for i in outs:
            if i.get("is_stake") is not None:
                del i["is_stake"]
        ret = {
            "vin": ins,
            "vout": outs,
            "txid": self.tx_id(),
            "total": total,
            "is_coinbase": is_coinbase,
            "timestamp": self._time,
        }
        return ret


class Daemon(object):

    def __init__(self, cfg):
        self._cfg_path = cfg
        self._explorer_cfg = get_explorer_config(self._cfg_path)
        self._cfg = self._explorer_cfg["wallet"]
        (self._addr, self._port,
         self._user, self._password) = self._validate_daemon_cfg(self._cfg)
        self._url = "http://%s:%s@%s:%s" % (self._user, self._password,
                                            self._addr, self._port)
        self._conn = AuthServiceProxy(self._url)
        self._db = Database(self._explorer_cfg, self._explorer_cfg["coin"])
        self._set_cwd()

    def _get_explorer_working_directory(self):
        here = os.path.abspath(os.path.dirname(self._cfg_path))
        return here

    def _set_cwd(self):
        wd = self._get_explorer_working_directory()
        os.chdir(wd)

    def _validate_daemon_cfg(self, cfg):
        addr = cfg.get("host")
        port = cfg.get("port")
        user = cfg.get("user")
        password = cfg.get("pass")
        if None in (addr, port, user, password):
            raise ValueError("Invalid config")
        return addr, port, user, password

    def call_method(self, method, *args):
        meth = getattr(self._conn, method)
        retried = getattr(self, "_retried", False)
        try:
            ret = meth(*args)
            self._retried = False
            return ret
        except Exception:
            if retried:
                raise
            self._retried = True
            self._conn = AuthServiceProxy(self._url)
            return self.call_method(method, *args)

    def blockchain_height(self):
        besthash = self.call_method("getbestblockhash")
        blk = self.call_method("getblock", besthash)
        return blk["height"]

    def get_block_hash(self, height):
        return self.call_method("getblockhash", height)

    def get_block(self, blkHash):
        # verbose=true showtxns=true
        blkDetails = self.call_method("getblock", blkHash, True, True)
        return blkDetails

    def get_block_at_height(self, height):
        blkhash = self.get_block_hash(height)
        return self.get_block(blkhash)

    def get_transaction(self, txid):
        tx = self.call_method("getrawtransaction", txid, 1)
        return tx

    def _get_coin_supply_coinbase(self):
        sent = self._db.get_address_info("coinbase")["sent"]
        return sent / NUM_UNITS

    def _get_coin_supply_getinfo(self):
        network = self._explorer_cfg.get("network")
        if (network == "mainnet"):
            # remove burned amount on mainnet
            return self.call_method("getinfo")["moneysupply"] - 112508403
        else:
            return self.call_method("getinfo")["moneysupply"]

    def get_coin_supply(self):
        supply_source = self._explorer_cfg.get("supply")
        if supply_source is None:
            return 0
        meth = getattr(self, "_get_coin_supply_%s" % supply_source.lower())
        if meth is None:
            logger.warning(
                "No method to get coin supply for %s" % supply_source)
            return 0
        return float(meth())

    def get_block_transactions(self, blk):
        transactions = []
        trx = blk.get("tx", [])
        if len(trx) == 0:
            return transactions

        for tx in trx:
            tpayTx = Tx(tx, self, blk["height"], blk["time"])
            details = tpayTx.details()
            has_token = False
            for o in details.get("vout", []):
                if (len(o["tokens"]) > 0):
                    has_token = True
                    break
            for i in details.get("vin", []):
                if (len(i["tokens"]) > 0):
                    has_token = True
                    break
            txInfo = {
                "txid" : details["txid"],
                "blockhash" : blk["hash"],
                "blockindex" : blk["height"],
                "timestamp" : details["timestamp"],
                "has_token" : has_token,
                "total" : details["total"],
                "vout" : details["vout"],
                "vin" : details["vin"],
                # not really needed. It is here for compatibility
                # with sync.js
                "__v" : 0
            }
            transactions.append(txInfo)
        return transactions

    def _wait_for_blockchain_sync(self):
        chain_height = self.blockchain_height()
        stats = self._db.get_stats()
        while int(stats["last"]) > int(chain_height):
            chain_height = self.blockchain_height()
            stats = self.db.get_stats()
            time.sleep(1)

    def _prepare_block(self, block):
        if block["height"] == 0:
            # Genesis
            prevhash = None
        else:
            prevhash = block["previousblockhash"]
        return {
            "height": block["height"],
            "hash": block["hash"],
            "prevhash": prevhash,
            "tx": [i["txid"] for i in block["tx"]],
        }


    def _ensure_blocks_collection_in_sync(self, last_height):
        """We added this collection. The explorer app
        does not have it. So we sync blocks up to the
        height the explorer managed to sync previously"""
        if last_height <= 1:
            return

        if last_height < 5000:
            start_block = 1
        else:
            start_block = last_height - 5000

        self._db.db.blocks.remove({})
        toInsert = []

        for i in range(start_block, last_height + 1):
            block = self.get_block_at_height(i)
            toInsert.append(self._prepare_block(block))
            if len(toInsert) >= 1000 or i == last_height:
                # flush to db
                logger.info(
                    "Flushing at height %s. "
                    "Chain height: %s" % (block["height"], last_height))
                self._db.db.blocks.insert_many(toInsert)
                toInsert = []

    def _update_stats(self, height, supply):
        stats = {
            "supply": supply,
            "block": height,
            "count": height - 1,
        }
        self._db.update_stats(stats)

    def _process_blocks(self):
        stats = self._db.get_stats()
        chain_height = self.blockchain_height()
        if int(stats["last"]) == int(chain_height):
            return

        diff = int(chain_height) - int(stats["last"])
        last_blk = self._db.get_last_recorded_block()
        last_height = stats["last"]
        logger.info("Last height is %d" % last_height)
        try:
            coin_supply = self.get_coin_supply()
        except Exception as err:
            logger.warning("Failed to get coin supply: %s" % err)
            coin_supply = 0
        blks = []
        txes = []
        partial_addrs = 0
        if last_height > 1:
            last_height += 1
        total_addrs = 0
        total_blks = 0
        total_txes = 0
        while last_height <= chain_height:
            blk = self.get_block_at_height(last_height)
            prev_blk = blk.get("previousblockhash")
            if last_blk and last_blk["hash"] != prev_blk:
                # chain reorg detected
                logger.info(
                    "Reorg detected: %s != %s. Rolling back "
                    "block %s" % (last_blk["hash"],
                    prev_blk, last_blk["height"]))
                self._db.rollback(last_blk["hash"])
                self._update_stats(last_blk["height"] - 1, coin_supply)
                raise ReorgException("Chain reorg detected")
            blks.append(self._prepare_block(blk))
            txes.extend(self.get_block_transactions(blk))
            if last_height % 1000 == 0 or last_height == chain_height:
                logger.info("commiting to database at block %r" % blk["height"])
                self._db.db.blocks.insert_many(blks)
                self._db.update_transactions(txes)
                addrs_touched = self._db.update_addresses(txes)
                self._update_stats(blk["height"], coin_supply)
                self._db.update_richlist()
                total_addrs += addrs_touched
                total_txes += len(txes)
                total_blks += len(blks)
                if len(blks) == 1000:
                    logger.info(
                        "Partial stats: Number of addresses touched: %d. "
                        "Number of transactions: %d. "  % (
                            addrs_touched, len(txes)))
                blks = []
                txes = []
            if last_height % 4000 == 0:
                logger.info(
                    "Prunning blocks collection older "
                    "than: %d" % (last_height - 5000))
                self._db.db.blocks.remove(
                    {"height": {"$lt": last_height - 5000}})
            last_blk = blk
            last_height += 1
        logger.info(
            "Finished updating blocks. Total addresses touched: %d, "
            "Total blocks processed: %d. Total transactions: %d" % (
                total_addrs, total_blks, total_txes))
        self._db.update_richlist()

    def _has_node(self):
        return shutil.which("node") is not None

    def _stop_process(self, pidfile):
        if os.path.isfile(pidfile) is False:
            return
        pid = open(pidfile).read()
        try:
            pid = int(pid)
            os.kill(pid, 9)
            os.remove(pidfile)
        except:
            return

    def _run_peers_sync(self):
        peers = "scripts/peers.js"
        if self._has_node:
            try:
                subprocess.check_call(
                    ["node", peers], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10)
            except Exception as err:
                logger.error("Failed to sync peers: %s" % err)
        else:
            logger.warning("nodejs not found. Skipping peers sync")

    def _run_markets_sync(self):
        sync = "scripts/sync.js"
        databases = ["index", "market"]
        if self._has_node:
            try:
                subprocess.check_call(
                    ["node", sync, "market"], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=10)
            except Exception as err:
                try:
                    for i in databases:
                        pidfile = "tmp/%s.pid" % i
                        self._stop_process(pidfile)
                except:
                    pass
                logger.error("Failed to sync markets: %s" % err)
        else:
            logger.warning("nodejs not found. Skipping market sync")

    def run(self):
        stats = self._db.get_stats()
        self._wait_for_blockchain_sync()
        self._ensure_blocks_collection_in_sync(stats["last"])
        while True:
            try:
                self._process_blocks()
                self._run_peers_sync()
                #self._run_markets_sync()
            except ReorgException:
                continue
            except Exception as err:
                logger.exception("got exception processing blocks")
            time.sleep(10)


if __name__ == "__main__":
    args = parser.parse_args()
    numeric_level = getattr(logging, args.loglevel, logging.INFO)
    log_format = '%(asctime)s %(name)s %(levelname)s %(message)s'
    logger = logging.getLogger('sync')
    logger.setLevel(numeric_level)
    formatter = logging.Formatter(log_format)
    if args.logfile:
        handler = logging.handlers.RotatingFileHandler(
              args.logfile, maxBytes=100*1024*1024, backupCount=2)
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    daemon = Daemon(args.explorer_config)
    daemon.run()