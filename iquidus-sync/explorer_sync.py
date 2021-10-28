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
import urllib.request
import zlib
import sys

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

initial_sync_done = False

# metadata cannot be gathered for these as they are invalid
# do not bother wasting time trying & retrying to get the metadata
invalid_token_ids = ['La77KcJTUj991FnvxNKhrCD1ER8S81T3LgECS6',
                     'La347xkKhi5VUCNDCqxXU4F1RUu8wPvC3pnQk6',
                     'La6gfSao2Qwmswzzf3rbn3hCzYtBntRUbSxfdF',
                     'La5wtUCjMyd5zRFWdW2jLVykct1nP57NPrJqaL',
                     'La531vUwiu9NnvtJcwPEjV84HrdKCupFCCb6D7',
                     'La62EhxMCGKFrqNMoDzYpHtwbPHXoDZRUKX2UU',
                     'La7BWZqFRSnJauaLQYniVYBh4KZkjgdTMRBNp7',
                     'La6iR2nU8pBuo9ozwihRrva8qDmA1gcSmus2sK',
                     'LaAHLLzkK8ar53vWyCZt4keCjC8Ea26Nv4pRNd',
                     'La3oUp5rGyAyyPDivRYCY5Q5GgrjoWSjsSU6aE',
                     'La9F14hMaUfn5mwkPkJQHXKmNVKsXfPq54SqK4',
                     'La977rwKFV5VZo3ABXi8Vr4X6jtRc9LdWqjZbt',
                     'La9rpjB2v9VHQbDa1gePZKUnwkdHZakXPgFn85',
                     'La9tKf3uoQvsiWyqUYDWWNxg9ofSZBkskVc2qh',
                     'La6gfSao2Qwmswzzf3rbn3hCzYtBntRUbSxfdF',
                     'La44xkuQuLT7P2bsszt3a6yKqN7E6rw75A8WDT']

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
                            break
                    if not token_exists:
                        details["tokens"].append({
                            "id": out_token["id"],
                            "received": int(out_token.get("amount", "0")),
                            "meta": out_token.get("meta", {})
                        })
                addrs[address] = details
            # add utxos with metadata to this token
            for out_token in out.get("tokens", []):
                self.add_metadata_utxo_to_token(out_token["id"], txid)
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
                            break
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

    def keyCleaner(self, d):
        if isinstance(d, (str, int, float)):
            return d
        if isinstance(d, dict):
            new = d.__class__()
            for k, v in d.items():
                new_key = k.replace('.','_')
                if new_key.startswith('$'):
                    new_key = '_' + new_key[:1]
                new[new_key] = self.keyCleaner(v)
        elif isinstance(d, (list, set, tuple)):
            new = d.__class__(self.keyCleaner(v) for v in d)
        else:
            return d
        return new

    def _prepare_ins_outs(self, transactions):
        addrs = {}
        for tx in transactions:
            vout = tx["vout"]
            vin = tx["vin"]
            addrs = self._process_vout(vout, tx["txid"], addrs)
            addrs = self._process_vin(vin, tx["txid"], addrs)
        return addrs

    def update_token(self, token_id, txid):
        if token_id in invalid_token_ids: return

        # insert/update metadata in the db
        token = self.db.tokens.find_one({"t_id": token_id})
        if token is None:
            tx = self.db.txes.find_one({"txid": txid})
            logger.info("Adding new token to the db: "+token_id)
            # build metadata of issuance
            found = False
            meta_of_issuance = {}
            issuance_address = ""
            issuance_txid = ""
            first_block = 0
            num_transfers = 0
            total_supply = 0
            aggregation_policy = ""
            lock_status = ""
            divisibility = 0
            for vout in tx.get("vout", []):
                for t in vout.get("tokens", []):
                    if token_id == t.get("id", ""):
                        found = True
                        meta_of_issuance["data"] = t.get("meta", {})
                        issuance_address = vout.get("addresses", "")
                        issuance_txid = t.get("issueTxid", "")
                        if issuance_txid != txid:
                            logger.warning("Issuance TXID does not match first txn token was seen!")
                            logger.warning("Token ID: " + token_id)
                            logger.warning("Issuance TXID: " + issuance_txid)
                            logger.warning("This TXID: " + txid)
                            logger.warning("This Amount: " + t.get("amount", 0))
                        first_block = tx.get("blockindex", 0)
                        # only locked supply is supported by the explorer right now
                        if t.get("lockStatus", "") == True:
                            total_supply = t.get("amount", 0)
                        aggregation_policy = t.get("aggregationPolicy", "")
                        lock_status = t.get("lockStatus", "")
                        divisibility = t.get("divisibility", 0)
                        break
                    if found:
                        break
                if found:
                    break


            self.db.tokens.insert_one(
                {
                    "t_id": token_id,
                    "meta_of_issuance": meta_of_issuance,
                    "issuance_address": issuance_address,
                    "issuance_txid": issuance_txid,
                    "first_block": first_block,
                    "num_transfers": num_transfers,
                    "total_supply": total_supply,
                    "aggregation_policy": aggregation_policy,
                    "lock_status": lock_status,
                    "divisibility": divisibility
                }
            )
        else:
            #logger.info("Updating token stats in the db: "+token_id)
            self.db.tokens.update_one(
                {"t_id": token_id},
                {
                    "$inc": {
                        "num_transfers": 1
                    }
                }
            )

    def add_metadata_utxo_to_token(self, token_id, txid):
        if token_id in invalid_token_ids: return
        self.update_token(token_id, txid)
        token = self.db.tokens.find_one({"t_id": token_id})
        if token is None:
            self.update_token(token_id, txid)
            token = self.db.tokens.find_one({"t_id": token_id})
        tx = self.db.txes.find_one({"txid": txid})
        metadata_size = 0
        serialized_metadata = ''
        for out in tx.get("vout", []):
            for out_token in out.get("tokens", {}):
                if out_token.get("id", "") == token_id:
                    meta_of_utxo = out_token.get("meta_of_utxo", {})
                    if meta_of_utxo is None:
                        return
                    serialized_metadata = json.dumps(meta_of_utxo,separators=(',', ':'))
                    metadata_size = len(serialized_metadata.encode())
                    if metadata_size > 2: # {} null object
                        break
            if metadata_size > 2:
                break
        if metadata_size == 2: # empty obj, return
            return
        z = zlib.compress(serialized_metadata.encode())
        utxo = {"txid": txid,
                "timestamp": tx.get("timestamp", 0),
                "metadata_size": metadata_size,
                "metadata_size_comp": len(z)}
        utxos = []
        if token is not None:
            utxos = token.get("metadata_utxos", [])
        else:
            # token is None, add debug logging
            logger.warn("Token not found in add_metadata_utxo_to_token")
            logger.warn("TokenID: " + token_id)
            logger.warn("TXID: " + txid)
        if utxo in utxos:
            return
        else:
            utxos.append(utxo)
            # limit utxo array size to 5000
            if len(utxos) > 5000:
                utxos = utxos[-5000:]
            self.db.tokens.update_one(
                {"t_id": token_id},
                {
                    "$set": {
                        "metadata_utxos": utxos
                    }
                }
            )

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
                # remove duplicates
                seen = set()
                txns = [x for x in txs if [(x['addresses']) not in seen, seen.add((x['addresses']))][0]]
                balance = received - sent
                addr_tokens = info.get("tokens", [])
                for tx_token in addrs[addr].get("tokens", []):
                    token_exists = False
                    for addr_token in addr_tokens:
                        if tx_token["id"] == addr_token["id"]:
                            addr_token["sent"] =  addr_token.get("sent", 0) + tx_token.get("sent", 0)
                            addr_token["received"] = addr_token.get("received", 0) + tx_token.get("received", 0)
                            addr_token["amount"] = addr_token["received"] - addr_token["sent"]
                            # self.update_token(tx_token["id"]);
                            token_exists = True
                            break
                    if not token_exists:
                        addr_token = {}
                        addr_token["id"] = tx_token["id"]
                        addr_token["sent"] = tx_token.get("sent", 0)
                        addr_token["received"] = tx_token.get("received", 0)
                        addr_token["amount"] = addr_token["received"] - addr_token["sent"]
                        addr_token["meta"] = tx_token["meta"]
                        addr_token = self.keyCleaner(addr_token)
                        addr_tokens.append(addr_token)
                        # self.update_token(tx_token["id"]);

                self.db.addresses.update_one(
                    {"a_id": addr},
                    {
                        "$set": {
                            "sent": sent,
                            "received": received,
                            "balance": balance,
                            "tokens": addr_tokens,
                            "txs": txns[-self._txcount:],
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
                    addr_token = self.keyCleaner(addr_token)
                    addr_tokens.append(addr_token)
                    # self.update_token(tx_token["id"]);
                sent = addrs[addr].get("sent", 0)
                received = addrs[addr].get("received", 0)
                txs = addrs[addr].get("txs", [])
                # remove duplicates
                seen = set()
                txns = [x for x in txs if [(x['addresses']) not in seen, seen.add((x['addresses']))][0]]
                balance = received - sent
                self.db.addresses.insert_one(
                    {
                        "a_id": addr,
                        "sent": sent,
                        "received": received,
                        "balance": balance,
                        "tokens": addr_tokens,
                        "txs": txns[-self._txcount:],
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
                        db_token_received = db_token.get("received", 0) - addr_token.get("received", 0)
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
        self.db.votes.delete_many({"block_hash": blockhash})


    def update_transactions(self, transactions):
        # convert to json and back to serialize all objs
        transactions = json.dumps(transactions)
        transactions = json.loads(transactions)
        transactions = self.keyCleaner(transactions)
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
        if "txes" not in names:
            self.db.create_collection("txes")
        if "addresses" not in names:
            self.db.create_collection("addresses")
        if "tokens" not in names:
            self.db.create_collection("tokens")
        if "peers" not in names:
            self.db.create_collection("peers")
        if "proposals" not in names:
            self.db.create_collection("proposals")
        if "votes" not in names:
            self.db.create_collection("votes")
        if "blocks" in names:
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
        if "proposals" in names:
            self.db.tokens.create_index("p_id")
            self.db.tokens.create_index("start_block")
            self.db.tokens.create_index("end_block")
        if "votes" in names:
            self.db.tokens.create_index("block_height")
            self.db.tokens.create_index("block_hash")
            self.db.tokens.create_index("proposal_id")
            self.db.tokens.create_index("staker_addr")
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
        global initial_sync_done

    def tx_id(self):
        return self._tx["txid"]

    def keyCleaner(self, d):
        if isinstance(d, (str, int, float)):
            return d
        if isinstance(d, dict):
            new = d.__class__()
            for k, v in d.items():
                new_key = k.replace('.','_')
                if new_key.startswith('$'):
                    new_key = '_' + new_key[:1]
                new[new_key] = self.keyCleaner(v)
        elif isinstance(d, (list, set, tuple)):
            new = d.__class__(self.keyCleaner(v) for v in d)
        else:
            return d
        return new

    def _get_token_metadata(self, token_id, utxo=None, retries=0):
        if token_id in invalid_token_ids: return {}
        if retries > 10: return {}
        try:
            data1 = urllib.request.urlopen(ntp1_api_url + 'tokenmetadata/' + token_id)
            metadata = json.loads(data1.read())
            metadata = self.keyCleaner(metadata) # remove '.' or '$' from keys
            someUtxo = metadata.get("someUtxo", "")
            #logger.info("Getting metdata for token: "+token_id)
        except Exception as err:
            logger.warning("RETRY: Error getting initial metadata for: %s" % err)
            logger.warning(token_id)
            time.sleep(10)
            retries += 1
            self._get_token_metadata(token_id, utxo, retries)
        finally:
            try:
                if data1: data1.close()
            except NameError:
                pass
        if (someUtxo):
            try:
                #logger.info("At UTXO: "+someUtxo)
                # get metadata at a specific utxo, if we do not have one, use someUtxo
                if utxo is None:
                  utxo = someUtxo
                data2 = urllib.request.urlopen(ntp1_api_url + 'tokenmetadata/' + token_id + '/' + utxo)
                metadata = json.loads(data2.read())
                metadata = self.keyCleaner(metadata) # remove '.' or '$' from keys
            except Exception as err:
                logger.warning("RETRY: Error getting extended metadata: %s" % err)
                logger.warning(token_id + "/" + utxo)
                if err.code == 500 and not initial_sync_done:
                    #if we get an HTTP 500 during initial sync we cannot get extended metadata for this UTXO, use someUtxo
                    retries += 1
                    logger.warning("Using someUtxo due to HTTP 500 " + someUtxo)
                    self._get_token_metadata(token_id, someUtxo, retries)
                else:
                    time.sleep(10)
                    retries += 1
                    self._get_token_metadata(token_id, utxo, retries)
            finally:
                try:
                    if data2: data2.close()
                except NameError:
                    pass
            # check for a firstBlock of -1
            if (metadata.get("firstBlock", 0) < 0):
                logger.warning("RETRY: Invalid first block in token metadata: %s" % err)
                time.sleep(10)
                retries += 1
                self._get_token_metadata(token_id, utxo, retries)
            return metadata
        else:
            logger.warning("RETRY:  No UTXO, cannot get token info for: "+token_id)
            time.sleep(10)
            retries += 1
            self._get_token_metadata(token_id, utxo, retries)

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
                type = scrypt.get("type", "")
                addr_index = 0
                if type == "coldstake":
                    addr_index = 1
                if addr is None:
                    addr = ["no address could be decoded",]
                # explorer expects id, not tokenId
                tokens = i.get("tokens", [])
                for t in tokens:
                    t["id"] = t.pop("tokenId")
                return {
                    "addresses": addr[addr_index],
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
                tx_meta_of_iss = t.get("metadataOfIssuance", {})
                if tx_meta_of_iss is not None:
                    tx_meta_data = tx_meta_of_iss.get("data", {})
                    t["meta"] = tx_meta_data
                else:
                    t["meta"] = {}
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
        txid = self._tx.get("txid", "")
        if len(vout) == 0:
            return ret
        tx = vout[0]

        is_nonstandard = tx["scriptPubKey"]["type"] == "nonstandard"
        is_stake = False
        is_cold_stake = False

        if is_nonstandard:
            vout.pop(0)
            if len(vout) == 0:
                return ret
            addr_index = 0
            type = vout[0]["scriptPubKey"].get("type", "")
            if type == "coldstake":
                addr_index = 1
                is_cold_stake = True
            addr = vout[0]["scriptPubKey"].get("addresses", [""])[addr_index]
            vin = self.inputs()
            if len(vin):
                is_stake = addr == vin[0]["addresses"]

        addrs = {}

        for i in vout:
            if self._output_is_valid(i) is False:
                continue
            script = i.get("scriptPubKey", {})
            addresses = script.get("addresses")
            type = script.get("type", "")
            addr_index = 0
            if type == "coldstake":
                addr_index = 1
                is_cold_stake = True
            if addresses is None:
                addr = "no address could be decoded"
            else:
                addr = addresses[addr_index]

            vout_tokens = i.get("tokens", [])
            for t in vout_tokens:
                # explorer expects id, not tokenId
                t["id"] = t.pop("tokenId")
                tx_meta_of_iss = t.get("metadataOfIssuance", {})
                if tx_meta_of_iss is not None:
                    tx_meta_data = tx_meta_of_iss.get("data", {})
                    t["meta"] = tx_meta_data
                else:
                    t["meta"] = {}
                tx_meta_of_utxo = self._tx.get("metadataOfUtxos", {})
                t["meta_of_utxo"] = tx_meta_of_utxo


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
                    "is_cold_stake": is_cold_stake,
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
        is_stake = False
        is_cold_stake = False
        for inp in ins:
            if(inp.get("tokens", [])):
                has_token_inputs = True
                break
        if len(outs) and outs[0]["is_stake"] and not has_token_inputs:
            ins = []

        for i in outs:
            if i.get("is_stake") is not None:
                is_stake = i["is_stake"]
                del i["is_stake"]
            if i.get("is_cold_stake") is not None:
                is_cold_stake = i["is_cold_stake"]
                del i["is_cold_stake"]
        ret = {
            "vin": ins,
            "vout": outs,
            "txid": self.tx_id(),
            "total": total,
            "is_coinbase": is_coinbase,
            "is_stake": is_stake,
            "is_cold_stake": is_cold_stake,
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
        except Exception as e:
            if retried:
                print(e)
                sys.exit(1)
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

    def rollback_to_height(self, height):
        stats = self._db.get_stats()
        rollback_height = stats["last"]
        tip_blk = self.get_block_at_height(rollback_height)
        prev_hash = tip_blk["hash"]
        while rollback_height > height:
            prev_blk = self.get_block(prev_hash)
            self._db.rollback(prev_hash)
            prev_hash = prev_blk["previousblockhash"]
            rollback_height = prev_blk["height"]
            if rollback_height % 100 == 0:
                logger.info("Rolled back to block: " + str(rollback_height))
        coin_supply = self.get_coin_supply()
        self._update_stats(rollback_height - 1, coin_supply)


    def get_block_transactions(self, blk):
        transactions = []
        trx = blk.get("tx", [])
        block_vote = blk.get("votevalue", None)
        if len(trx) == 0:
            return transactions

        for tx in trx:
            tpayTx = Tx(tx, self, blk["height"], blk["time"])
            details = tpayTx.details()
            has_token = False
            has_block_vote = False
            for o in details.get("vout", []):
                if (len(o["tokens"]) > 0):
                    has_token = True
                    break
            for i in details.get("vin", []):
                if (len(i["tokens"]) > 0):
                    has_token = True
                    break
            if details["is_stake"] or details["is_cold_stake"]:
                if block_vote is not None:
                    has_block_vote = True

            txInfo = {
                "txid" : details["txid"],
                "blockhash" : blk["hash"],
                "blockindex" : blk["height"],
                "timestamp" : details["timestamp"],
                "has_token" : has_token,
                "has_block_vote": has_block_vote,
                "is_cold_stake" : details["is_cold_stake"],
                "total" : details["total"],
                "vout" : details["vout"],
                "vin" : details["vin"],
                # not really needed. It is here for compatibility
                # with sync.js
                "__v" : 0
            }
            transactions.append(txInfo)
        return transactions

    def get_block_vote(self, blk):
        block_vote = blk.get("votevalue", None)
        if block_vote is None:
            return block_vote

        block_height = blk.get("height", None)
        block_hash = blk.get("hash", None)
        proposal_id = block_vote.get("ProposalID", None)
        vote_value = block_vote.get("VoteValue", None)
        # for now, only allow 1/0 (yea/nay) votes
        if vote_value == 0:
            vote_value = 'Nay'
        elif vote_value == 1:
            vote_value = 'Yea'
        else:
            #invalid vote
            return None

        staker_addr = None

        # get staker addr
        txs = blk.get("tx", None)
        if txs is not None and len(txs) > 1:
            stake_vout = txs[1].get("vout", None)
            if stake_vout is not None and len(stake_vout) > 0:
                # loop over vouts
                for i in stake_vout:
                    spk = i.get("scriptPubKey", None)
                    addr_index = 0
                    if spk is not None:
                        spk_type = spk.get("type", None)
                        if spk_type == "coldstake":
                            addr_index = 1
                        addrs = spk.get("addresses", None)
                        if addrs is not None and len(addrs) > 0:
                            staker_addr = addrs[addr_index]
                            break

        return {
            "block_height": block_height,
            "block_hash": block_hash,
            "proposal_id": proposal_id,
            "vote_value": vote_value,
            "staker_addr": staker_addr,
        }

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
        next_block_hash = None

        for i in range(start_block, last_height + 1):
            if next_block_hash is not None:
                block = self.get_block(next_block_hash)
            else:
                block = self.get_block_at_height(i)
            next_block_hash = block.get("nextblockhash", None)
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
        global initial_sync_done
        if int(stats["last"]) == int(chain_height):
            return

        diff = int(chain_height) - int(stats["last"])
        last_blk = self._db.get_last_recorded_block()
        last_height = stats["last"]
        next_block_hash = None
        if last_height > (chain_height - 100):
            initial_sync_done = True
        logger.info("Last height is %d" % last_height)
        try:
            coin_supply = self.get_coin_supply()
        except Exception as err:
            logger.warning("Failed to get coin supply: %s" % err)
            coin_supply = 0
        blks = []
        txes = []
        votes = []
        partial_addrs = 0
        if last_height > 1:
            last_height += 1
        total_addrs = 0
        total_blks = 0
        total_txes = 0
        while last_height <= chain_height:
            if next_block_hash is not None:
                blk = self.get_block(next_block_hash)
            else:
                blk = self.get_block_at_height(last_height)
            prev_blk = blk.get("previousblockhash")
            next_block_hash = blk.get("nextblockhash", None)
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
            blk_vote = self.get_block_vote(blk)
            if blk_vote is not None:
                votes.append(blk_vote)
            if last_height % 1000 == 0 or last_height == chain_height:
                logger.info("commiting to database at block %r" % blk["height"])
                self._db.db.blocks.insert_many(blks)
                self._db.update_transactions(txes)
                if len(votes) > 0:
                    self._db.db.votes.insert_many(votes)
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
                votes = []
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

    def _run_proposals_sync(self):
        logger.info("Syncing proposals from GitHub")
        proposals_data = urllib.request.urlopen('https://raw.githubusercontent.com/NeblioTeam/Neblio-Improvement-Proposals/main/proposals.json')
        proposals = proposals_data.read()
        proposals_json = json.loads(proposals.decode('utf-8'))
        network = self._explorer_cfg.get("network")
        stats = self._db.get_stats()
        last_block_height = int(stats["last"])
        for p in proposals_json:
            if p['network'] == network:

                # set status based on block height
                status = "unknown"
                if last_block_height < p["start_block"]:
                    status = "upcoming"
                elif last_block_height >= p["start_block"] and last_block_height <= p["end_block"]:
                    status = "in_progress"
                # don't mark as completed until a 20 block buffer is in
                # This will prevent reorgs from affecting the last few
                # blocks since we will be copying the votes to the
                # proposal entry in the DB upton completion
                elif last_block_height > (p["end_block"] + 20):
                    status = "completed"

                completed_votes = {}
                # if the vote is over, copy the vote details here for easy future lookup
                if status == "completed":
                    votes = self._db.db.votes.find({"proposal_id": p["proposal_id"]})
                    # find the total number of eligible votes
                    total_votes = (p["end_block"] - p["start_block"] + 1)
                    completed_votes["Yea"] = 0
                    completed_votes["Nay"] = 0
                    completed_votes["no_vote"] = 0
                    for v in votes:
                        if (v["vote_value"] == "Yea"):
                            completed_votes["Yea"] = completed_votes["Yea"] + 1
                        elif (v["vote_value"] == "Nay"):
                            completed_votes["Nay"] = completed_votes["Nay"] + 1
                    completed_votes["no_vote"] = total_votes - completed_votes["Yea"] - completed_votes["Nay"]

                self._db.db.proposals.update_one(
                    {"p_id": p["proposal_id"]},
                    {
                        "$set": {
                            "name": p["proposal_name"],
                            "desc": p["proposal_desc"],
                            "url": 'https://github.com/NeblioTeam/Neblio-Improvement-Proposals/issues/' + str(p["proposal_id"]),
                            "start_block": p["start_block"],
                            "end_block": p["end_block"],
                            "status": status,
                            "completed_votes": completed_votes,
                        }
                    }, upsert=True)


    def run(self):
        count = 0
        stats = self._db.get_stats()
        self._wait_for_blockchain_sync()
        self._ensure_blocks_collection_in_sync(stats["last"])
        # FOR TESTING ONLY, DO NOT USE IN PRODUCTION
        # self.rollback_to_height(2972177)
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
            if count % 100 == 0:
                self._run_proposals_sync()
                count = 0
            count = count + 1


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
