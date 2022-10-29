var request = require('request')
  , settings = require('./settings')
  , Token = require('../models/token')
  , Address = require('../models/address');

var base_url = 'http://127.0.0.1:' + settings.port + '/api/';


// returns coinbase total sent as current coin supply
function coinbase_supply(cb) {
  Address.findOne({a_id: 'coinbase'}, function(err, address) {
    if (address) {
      return cb(address.sent);
    } else {
      return cb();
    }
  });
}

// returns metadata for a token
function token_metadata(tokenID, cb) {
  //console.log("Looking for tokenid: " + tokenID);
  Token.findOne({t_id:tokenID}, function(err, token) {
    if(token) {
      //console.log("Got tokenid: " + tokenID);
      return cb(token);
    } else {
      return cb();
    }
  });
}

module.exports = {

  convert_to_satoshi: function(amount, cb) {
    // fix to 8dp & convert to string
    var fixed = amount.toFixed(8).toString();
    // remove decimal (.) and return integer
    return cb(parseInt(fixed.replace('.', '')));
  },

  get_hashrate: function(cb) {
    if (settings.index.show_hashrate == false) return cb('-');
    if (settings.nethash == 'netmhashps') {
      var uri = base_url + 'getmininginfo';
      request({uri: uri, json: true, timeout: 2000}, function (error, response, body) { //returned in mhash
        if (body.netmhashps) {
          if (settings.nethash_units == 'K') {
            return cb((body.netmhashps * 1000).toFixed(4));
          } else if (settings.nethash_units == 'G') {
            return cb((body.netmhashps / 1000).toFixed(4));
          } else if (settings.nethash_units == 'H') {
            return cb((body.netmhashps * 1000000).toFixed(4));
          } else if (settings.nethash_units == 'T') {
            return cb((body.netmhashps / 1000000).toFixed(4));
          } else if (settings.nethash_units == 'P') {
            return cb((body.netmhashps / 1000000000).toFixed(4));
          } else {
            return cb(body.netmhashps.toFixed(4));
          }
        } else {
          return cb('-');
        }
      });
    } else {
      var uri = base_url + 'getnetworkhashps';
      request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
        if (body == 'There was an error. Check your console.') {
          return cb('-');
        } else {
          if (settings.nethash_units == 'K') {
            return cb((body / 1000).toFixed(4));
          } else if (settings.nethash_units == 'M'){
            return cb((body / 1000000).toFixed(4));
          } else if (settings.nethash_units == 'G') {
            return cb((body / 1000000000).toFixed(4));
          } else if (settings.nethash_units == 'T') {
            return cb((body / 1000000000000).toFixed(4));
          } else if (settings.nethash_units == 'P') {
            return cb((body / 1000000000000000).toFixed(4));
          } else {
            return cb((body).toFixed(4));
          }
        }
      });
    }
  },


  get_difficulty: function(cb) {
    var uri = base_url + 'getdifficulty';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_connectioncount: function(cb) {
    var uri = base_url + 'getconnectioncount';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_blockcount: function(cb) {
    var uri = base_url + 'getblockcount';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_blockhash: function(height, cb) {
    var uri = base_url + 'getblockhash?height=' + height;
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_block: function(hash, cb) {
    var uri = base_url + 'getblock?hash=' + hash;
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_rawtransaction: function(hash, cb) {
    var uri = base_url + 'getrawtransaction?txid=' + hash + '&decrypt=1';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_maxmoney: function(cb) {
    var uri = base_url + 'getmaxmoney';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_maxvote: function(cb) {
    var uri = base_url + 'getmaxvote';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_vote: function(cb) {
    var uri = base_url + 'getvote';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_phase: function(cb) {
    var uri = base_url + 'getphase';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_reward: function(cb) {
    var uri = base_url + 'getreward';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_estnext: function(cb) {
    var uri = base_url + 'getnextrewardestimate';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  get_nextin: function(cb) {
    var uri = base_url + 'getnextrewardwhenstr';
    request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
      return cb(body);
    });
  },

  // synchonous loop used to interate through an array,
  // avoid use unless absolutely neccessary
  syncLoop: function(iterations, process, exit){
    var index = 0,
        done = false,
        shouldExit = false;
    var loop = {
      next:function(){
          if(done){
              if(shouldExit && exit){
                  exit(); // Exit if we're done
              }
              return; // Stop the loop if we're done
          }
          // If we're not finished
          if(index < iterations){
              index++; // Increment our index
              if (index % 100 === 0) { //clear stack
                setTimeout(function() {
                  process(loop); // Run our process, pass in the loop
                }, 1);
              } else {
                 process(loop); // Run our process, pass in the loop
              }
          // Otherwise we're done
          } else {
              done = true; // Make sure we say we're done
              if(exit) exit(); // Call the callback on exit
          }
      },
      iteration:function(){
          return index - 1; // Return the loop number we're on
      },
      break:function(end){
          done = true; // End the loop
          shouldExit = end; // Passing end as true means we still call the exit callback
      }
    };
    loop.next();
    return loop;
  },

  balance_supply: function(cb) {
    Address.find({}, 'balance').where('balance').gt(0).exec(function(err, docs) {
      var count = 0;
      module.exports.syncLoop(docs.length, function (loop) {
        var i = loop.iteration();
        count = count + docs[i].balance;
        loop.next();
      }, function(){
        return cb(count);
      });
    });
  },

  get_supply: function(cb) {
    if ( settings.supply == 'HEAVY' ) {
      var uri = base_url + 'getsupply';
      request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
        return cb(body);
      });
    } else if (settings.supply == 'GETINFO') {
      var uri = base_url + 'getinfo';
      request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
        // return cb(body.moneysupply);
        if(!body) return cb(0);
        if (settings.network == 'testnet') {
        	return cb(body.moneysupply);
        } else {
	        //subtract burnt amount from supply
	        return cb(body.moneysupply-112508402.70606036);
	    }
      });
    } else if (settings.supply == 'BALANCES') {
      module.exports.balance_supply(function(supply) {
        return cb(supply/100000000);
      });
    } else if (settings.supply == 'TXOUTSET') {
      var uri = base_url + 'gettxoutsetinfo';
      request({uri: uri, json: true, timeout: 2000}, function (error, response, body) {
        return cb(body.total_amount);
      });
    } else {
      coinbase_supply(function(supply) {
        return cb(supply/100000000);
      });
    }
  },

  is_unique: function(array, object, cb) {
    var unique = true;
    var index = null;
    module.exports.syncLoop(array.length, function (loop) {
      var i = loop.iteration();
      if (array[i].addresses == object) {
        unique = false;
        index = i;
        loop.break(true);
        loop.next();
      } else {
        loop.next();
      }
    }, function(){
      return cb(unique, index);
    });
  },

  calculate_total: function(vout, cb) {
    var total = 0;
    module.exports.syncLoop(vout.length, function (loop) {
      var i = loop.iteration();
      //module.exports.convert_to_satoshi(parseFloat(vout[i].amount), function(amount_sat){
        total = total + vout[i].amount;
        loop.next();
      //});
    }, function(){
      return cb(total);
    });
  },

  prepare_vout: function(vout, tx_ntp1, txid, vin, cb) {
    var arr_vout = [];
    var arr_vin = [];
    arr_vin = vin;
    var contains_tokens = false
    module.exports.syncLoop(vout.length, function (loop) {
      var i = loop.iteration();
      // check for OP_RETURN to assume if txn is NTP1
      if (vout[i].scriptPubKey.type == 'nulldata') {
        contains_tokens = true;
      }
      // make sure vout has an address
      if (vout[i].scriptPubKey.type != 'nonstandard' && vout[i].scriptPubKey.type != 'nulldata') {
        // check if vout address is unique, if so add it array, if not add its amount to existing index
        //console.log('vout:' + i + ':' + txid);
        module.exports.is_unique(arr_vout, vout[i].scriptPubKey.addresses[0], function(unique, index) {
          if (unique == true) {
            // unique vout
            module.exports.convert_to_satoshi(parseFloat(vout[i].value), function(amount_sat){
              if (tx_ntp1.vout && tx_ntp1.vout.length) {
                module.exports.get_tx_tokens(tx_ntp1.vout[i], function(tokens) {
                  if (tokens.length) {
                    arr_vout.push({addresses: vout[i].scriptPubKey.addresses[0], tokens: tokens, amount: amount_sat});
                    loop.next();
                  } else {
                    arr_vout.push({addresses: vout[i].scriptPubKey.addresses[0], amount: amount_sat});
                    loop.next();
                  }
                });
              } else {
                loop.next();
              }
            });
          } else {
            // already exists
            module.exports.convert_to_satoshi(parseFloat(vout[i].value), function(amount_sat){
              module.exports.get_tx_tokens(tx_ntp1.vout[i], function(tokens) {
                if (tokens.length) {
                  // if there are already tokens at this vout index, concat with existing
                  // else set vout tokens to tokens
                  if (arr_vout[index].tokens && arr_vout[index].tokens.length > 0) {
                    arr_vout[index].tokens = arr_vout[index].tokens.concat(tokens);
                    arr_vout[index].amount = arr_vout[index].amount + amount_sat;
                    loop.next();
                  } else {
                    arr_vout[index].tokens = tokens;
                    arr_vout[index].amount = arr_vout[index].amount + amount_sat;
                    loop.next();
                  }
                } else {
                  arr_vout[index].amount = arr_vout[index].amount + amount_sat;
                  loop.next();
                }
              });
            });
          }
        });
      } else {
        // no address, move to next vout
        loop.next();
      }
    }, function(){
      if (vout[0].scriptPubKey.type == 'nonstandard') {
        if ( arr_vin.length > 0 && arr_vout.length > 0 ) {
          if (arr_vin[0].addresses == arr_vout[0].addresses) {
            // do not mark malformed NTP1 txns as PoS
          	if(!contains_tokens && !arr_vin[0].tokens) {
              // PoS
              arr_vout[0].amount = arr_vout[0].amount - arr_vin[0].amount;
              arr_vin.shift();
              return cb(arr_vout, arr_vin);
            } else {
              return cb(arr_vout, arr_vin);
            }
          } else {
            return cb(arr_vout, arr_vin);
          }
        } else {
          return cb(arr_vout, arr_vin);
        }
      } else {
        return cb(arr_vout, arr_vin);
      }
    });
  },

  get_input_addresses: function(input, vout, cb) {
    var addresses = [];
    if (input.coinbase) {
      var amount = 0;
      module.exports.syncLoop(vout.length, function (loop) {
        var i = loop.iteration();
          amount = amount + parseFloat(vout[i].value);
          loop.next();
      }, function(){
        addresses.push({hash: 'coinbase', amount: amount});
        return cb(addresses);
      });
    } else {
      module.exports.get_rawtransaction(input.txid, function(tx){
        if (tx) {
          module.exports.syncLoop(tx.vout.length, function (loop) {
            var i = loop.iteration();
            if (tx.vout[i].n == input.vout) {
              //module.exports.convert_to_satoshi(parseFloat(tx.vout[i].value), function(amount_sat){
              if (tx.vout[i].scriptPubKey.addresses) {
                addresses.push({hash: tx.vout[i].scriptPubKey.addresses[0], amount:tx.vout[i].value});
              }
                loop.break(true);
                loop.next();
              //});
            } else {
              loop.next();
            }
          }, function(){
            return cb(addresses);
          });
        } else {
          return cb();
        }
      });
    }
  },

  prepare_vin: function(tx, tx_ntp1, cb) {
    var arr_vin = [];
    module.exports.syncLoop(tx.vin.length, function (loop) {
      var i = loop.iteration();
      module.exports.get_input_addresses(tx.vin[i], tx.vout, function(addresses){
        if (addresses && addresses.length) {
          module.exports.is_unique(arr_vin, addresses[0].hash, function(unique, index) {
            if (unique == true) {
              module.exports.convert_to_satoshi(parseFloat(addresses[0].amount), function(amount_sat){
                if(tx_ntp1.vin && tx_ntp1.vin.length) {
                  module.exports.get_tx_tokens(tx_ntp1.vin[i], function(tokens) {
                    if (tokens.length > 0) {
                      arr_vin.push({addresses:addresses[0].hash, tokens: tokens, amount:amount_sat});
                    } else {
                      arr_vin.push({addresses:addresses[0].hash, amount:amount_sat});
                    }
                    loop.next();
                  });
                } else {
                  loop.next();
                }
              });
            } else {
              // already exists
              module.exports.convert_to_satoshi(parseFloat(addresses[0].amount), function(amount_sat){
                module.exports.get_tx_tokens(tx_ntp1.vin[i], function(tokens) {
                  if (tokens.length > 0) {
                    // if there are already tokens at this vout index, concat with existing
                    // else set vout tokens to tokens
                    if (arr_vin[index].tokens && arr_vin[index].tokens.length > 0) {
                      arr_vin[index].tokens = arr_vin[index].tokens.concat(tokens);
                      arr_vin[index].amount = arr_vin[index].amount + amount_sat;
                      loop.next();
                    } else {
                      arr_vin[index].tokens = tokens;
                      arr_vin[index].amount = arr_vin[index].amount + amount_sat;
                      loop.next();
                    }
                  } else {
                    arr_vin[index].amount = arr_vin[index].amount + amount_sat;
                    loop.next();
                  }
                });
              });
            }
          });
        } else {
          loop.next();
        }
      });
    }, function(){
      return cb(arr_vin);
    });
  },

  get_ntp1_transaction: function(hash, cb) {
    var uri = settings.ntp1api.url + 'transactioninfo/' + hash;
    request({uri: uri, json: true, timeout: 120000}, function (error, response, body) {
      if (error) {
        console.log('da', error);
      }
      return cb(body);
    });
  },

  get_ntp1_tokenmetadata: function(tokenId, cb) {
    var uri = settings.ntp1api.url + 'tokenmetadata/' + tokenId;
    request({uri: uri, json: true, timeout: 120000}, function (error, response, body) {
      if (error) {
        console.log('da', error);
      }
      if (!body) return;
      var uri2 = settings.ntp1api.url + 'tokenmetadata/' + tokenId + '/' + body.someUtxo;
      request({uri: uri2, json: true, timeout: 120000}, function (error2, response2, body2) {
        if (error2) {
          console.log(error2);
        }

        return cb(body2);
      });
    });
  },

  get_tx_tokens: function(v, cb) {
    var tokens = [];
    if (v.tokens && v.tokens.length) {
      var counter = 0;
      module.exports.syncLoop(v.tokens.length, function (loop) {
        var i = loop.iteration();
        //var counter = 0;

        // first check the local token db for our token info
        token_metadata(v.tokens[i].tokenId, function(token_db) {
          if (token_db) {
            if (token_db.meta_of_issuance) {
              tokens.push({id:v.tokens[i].tokenId, amount:v.tokens[i].amount, meta:token_db.meta_of_issuance.data});
            } else {
              tokens.push({id:v.tokens[i].tokenId, amount:v.tokens[i].amount});
            }

            if ((tokens.length + counter) == v.tokens.length) {
              return cb(tokens);
            } else {
              loop.next();
            }
          } else {
            // handle API call case
            module.exports.get_ntp1_tokenmetadata(v.tokens[i].tokenId, function(token) {
              if (token) {
                if (token.metadataOfIssuence) {
                  tokens.push({id:v.tokens[i].tokenId, amount:v.tokens[i].amount, meta:token.metadataOfIssuence.data});
                } else {
                  tokens.push({id:v.tokens[i].tokenId, amount:v.tokens[i].amount});
                }
              } else {
                // no tokens were found in db or with API, NEXT
                counter = counter + 1;
                loop.next();
              }

              if ((tokens.length + counter) == v.tokens.length) {
                return cb(tokens);
              } else {
                loop.next();
              }
            });
          }
        });
      }, function() {});
    } else {
      return cb(tokens);
    }
  }
};
