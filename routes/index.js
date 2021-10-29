var express = require('express')
  , router = express.Router()
  , request = require('request')
  , fs = require('fs')
  , settings = require('../lib/settings')
  , locale = require('../lib/locale')
  , db = require('../lib/database')
  , lib = require('../lib/explorer')
  , qr = require('qr-image');

function route_get_block(res, blockhash) {
  lib.get_block(blockhash, function (block) {
    if (block != 'There was an error. Check your console.') {
      if (blockhash == settings.genesis_block) {
        res.render('block', { active: 'block', block: block, confirmations: settings.confirmations, txs: 'GENESIS'});
      } else {
        db.get_txs(block, function(txs) {
          if (txs.length > 0) {
            db.get_block_vote(blockhash, function(vote) {
              if (vote) {
                res.render('block', { active: 'block', block: block, confirmations: settings.confirmations, txs: txs, vote: vote});
              } else {
                res.render('block', { active: 'block', block: block, confirmations: settings.confirmations, txs: txs});
              }
            });
          } else {
            route_get_index(res, 'Block not found: ' + blockhash);
            // db.create_txs(block, function(){
            //   db.get_txs(block, function(ntxs) {
            //     if (ntxs.length > 0) {
            //       res.render('block', { active: 'block', block: block, confirmations: settings.confirmations, txs: ntxs});
            //     } else {
            //       route_get_index(res, 'Block not found: ' + blockhash);
            //     }
            //   });
            // });
          }
        });
      }
    } else {
      route_get_index(res, 'Block not found: ' + blockhash);
    }
  });
}
/* GET functions */

function route_get_tx(res, txid) {
  if (txid == settings.genesis_tx) {
    route_get_block(res, settings.genesis_block);
  } else {
    db.get_tx(txid, function(tx) {
      if (tx) {
        lib.get_blockcount(function(blockcount) {
          res.render('tx', { active: 'tx', tx: tx, confirmations: settings.confirmations, blockcount: blockcount});
        });
      }
      else {
        route_get_index(res, 'TX not found: ' + txid);
        // lib.get_rawtransaction(txid, function(rtx) {
        //   if (rtx.txid) {
        //     lib.prepare_vin(rtx, function(vin) {
        //       lib.prepare_vout(rtx.vout, rtx.txid, vin, function(rvout, rvin) {
        //         lib.calculate_total(rvout, function(total){
        //           if (!rtx.confirmations > 0) {
        //             var utx = {
        //               txid: rtx.txid,
        //               vin: rvin,
        //               vout: rvout,
        //               total: total.toFixed(8),
        //               timestamp: rtx.time,
        //               blockhash: '-',
        //               blockindex: -1,
        //             };
        //             res.render('tx', { active: 'tx', tx: utx, confirmations: settings.confirmations, blockcount:-1});
        //           } else {
        //             var utx = {
        //               txid: rtx.txid,
        //               vin: rvin,
        //               vout: rvout,
        //               total: total.toFixed(8),
        //               timestamp: rtx.time,
        //               blockhash: rtx.blockhash,
        //               blockindex: rtx.blockheight,
        //             };
        //             lib.get_blockcount(function(blockcount) {
        //               res.render('tx', { active: 'tx', tx: utx, confirmations: settings.confirmations, blockcount: blockcount});
        //             });
        //           }
        //         });
        //       });
        //     });
        //   } else {
        //     route_get_index(res, null);
        //   }
        // });
      }
    });
  }
}

function route_get_index(res, error) {
  res.render('index', { active: 'home', error: error, warning: null});
}

function route_get_address(res, hash, count) {
  db.get_address(hash, function(address) {
    if (address) {
      var txs = [];
      var block_votes = {}
      db.get_block_votes_by_address(hash, function(votes) {
        if (votes.length > 0) {
          lib.syncLoop(votes.length, function (loop) {
            var i = loop.iteration();
            if (!block_votes.hasOwnProperty(votes[i].proposal_id)) {
              block_votes[votes[i].proposal_id] = {}
              block_votes[votes[i].proposal_id]['Yea'] = 0
              block_votes[votes[i].proposal_id]['Nay'] = 0
            }
            if (votes[i].vote_value == 'Yea') {
              block_votes[votes[i].proposal_id]['Yea'] = block_votes[votes[i].proposal_id]['Yea'] + 1
            }
            if (votes[i].vote_value == 'Nay') {
              block_votes[votes[i].proposal_id]['Nay'] = block_votes[votes[i].proposal_id]['Nay'] + 1
            }
            loop.next();
          });
        } else {
          block_votes = {}
        }
        var hashes = address.txs.reverse();
        if (address.txs.length < count) {
          count = address.txs.length;
        }
        lib.syncLoop(count, function (loop) {
          var i = loop.iteration();
          db.get_tx(hashes[i].addresses, function(tx) {
            if (tx) {
              txs.push(tx);
              loop.next();
            } else {
              loop.next();
            }
          });
        }, function(){

          res.render('address', { active: 'address', address: address, txs: txs, votes: block_votes});
        });
      });
    } else {
      route_get_index(res, hash + ' not found');
    }
  });
}

function route_get_token(res, tokenId) {
  db.get_token(tokenId, function(token) {
    if (token) {
      res.render('token', {active: 'token', token: token});
    } else {
      route_get_index(res, tokenId + ' not found');
    }
  });
}

/* GET home page. */
router.get('/', function(req, res) {
  route_get_index(res, null);
});

router.get('/info', function(req, res) {
  res.render('info', { active: 'info', address: settings.address, hashes: settings.api });
});

router.get('/markets/:market', function(req, res) {
  var market = req.params['market'];
  if (settings.markets.enabled.indexOf(market) != -1) {
    db.get_market(market, function(data) {
      /*if (market === 'bittrex') {
        data = JSON.parse(data);
      }*/
      console.log(data);
      res.render('./markets/' + market, {
        active: 'markets',
        marketdata: {
          coin: settings.markets.coin,
          exchange: settings.markets.exchange,
          data: data,
        },
        market: market
      });
    });
  } else {
    route_get_index(res, null);
  }
});

router.get('/richlist', function(req, res) {
  if (settings.display.richlist == true ) {
    db.get_stats(settings.coin, function (stats) {
      db.get_richlist(settings.coin, function(richlist){
        //console.log(richlist);
        if (richlist) {
          db.get_distribution(richlist, stats, function(distribution) {
            //console.log(distribution);
            res.render('richlist', {
              active: 'richlist',
              balance: richlist.balance,
              received: richlist.received,
              stats: stats,
              dista: distribution.t_1_25,
              distb: distribution.t_26_50,
              distc: distribution.t_51_75,
              distd: distribution.t_76_100,
              diste: distribution.t_101plus,
              show_dist: settings.richlist.distribution,
              show_received: settings.richlist.received,
              show_balance: settings.richlist.balance,
            });
          });
        } else {
          route_get_index(res, null);
        }
      });
    });
  } else {
    route_get_index(res, null);
  }
});

router.get('/movement', function(req, res) {
  res.render('movement', {active: 'movement', flaga: settings.movement.low_flag, flagb: settings.movement.high_flag, min_amount:settings.movement.min_amount});
});

router.get('/network', function(req, res) {
  res.render('network', {active: 'network'});
});

router.get('/reward', function(req, res){
  //db.get_stats(settings.coin, function (stats) {
    console.log(stats);
    db.get_heavy(settings.coin, function (heavy) {
      //heavy = heavy;
      var votes = heavy.votes;
      votes.sort(function (a,b) {
        if (a.count < b.count) {
          return -1;
        } else if (a.count > b.count) {
          return 1;
        } else {
         return 0;
        }
      });

      res.render('reward', { active: 'reward', stats: stats, heavy: heavy, votes: heavy.votes });
    });
  //});
});

router.get('/voting', function(req, res){
  db.get_all_proposals(function (proposals) {
    db.get_votes_for_active_proposals(function (votes) {
    	db.get_stats(settings.coin, function (stats) {
	      upcoming_proposals = []
	      in_progress_proposals = []
	      completed_proposals = []
	      active_votes = {}
	      last_block = stats["last"]
	      // sort our active votes per each proposal
	      lib.syncLoop(votes.length, function (loop) {
	      	var i = loop.iteration();
	      	if (!(votes[i]["proposal_id"] in active_votes)) {
	      		active_votes[votes[i]["proposal_id"] = {}
	      		active_votes[votes[i]["proposal_id"]]['Yea'] = 0
	      		active_votes[votes[i]["proposal_id"]]['Nay'] = 0
	      	}
	      	active_votes[votes[i]["proposal_id"]][votes[i]["vote_value"]] = active_votes[votes[i]["proposal_id"]][votes[i]["vote_value"]] + 1
	      	loop.next();
	      });

	      // sort our proposals based on status
	      lib.syncLoop(proposals.length, function (loop) {
	        var i = loop.iteration();
	        if (proposals[i]["status"] == "upcoming") {
	          upcoming_proposals.push(proposals[i])
	        } else if (proposals[i]["status"] == "in_progress") {
	          in_progress_proposals.push(proposals[i])
	          active_votes[proposals[i]["p_id"]]["no_vote"] = last_block - proposals[i]["start_block"] + 1 - active_votes[proposals[i]["p_id"]]["Yea"] - active_votes[proposals[i]["p_id"]]["Nay"]
	        } else if (proposals[i]["status"] == "completed") {
	          completed_proposals.push(proposals[i])
	        }
	        loop.next();
	      });
	      res.render('voting', { active: 'voting', upcoming_proposals: upcoming_proposals, in_progress_proposals: in_progress_proposals, completed_proposals: completed_proposals, active_votes: votes});
      });
    });
  });
});

router.get('/token', function(req, res){
  db.get_tokens(function (tokens) {
    res.render('tokens', {active: 'token', tokens: tokens });
  });
});

router.get('/token/:tokenId', function(req, res) {
  route_get_token(res, req.param('tokenId'));
});

router.get('/tx/:txid', function(req, res) {
  route_get_tx(res, req.param('txid'));
});

router.get('/block/:hash', function(req, res) {
  route_get_block(res, req.param('hash'));
});

router.get('/address/:hash', function(req, res) {
  route_get_address(res, req.param('hash'), settings.txcount);
});

router.get('/address/:hash/:count', function(req, res) {
  route_get_address(res, req.param('hash'), req.param('count'));
});

router.post('/search', function(req, res) {
  var query = req.body.search;
  if (query.length == 64) {
    if (query == settings.genesis_tx) {
      res.redirect('/block/' + settings.genesis_block);
    } else {
      db.get_tx(query, function(tx) {
        if (tx) {
          res.redirect('/tx/' +tx.txid);
        } else {
          lib.get_block(query, function(block) {
            if (block != 'There was an error. Check your console.') {
              res.redirect('/block/' + query);
            } else {
              route_get_index(res, locale.ex_search_error + query );
            }
          });
        }
      });
    }
  } else {
    db.get_address(query, function(address) {
      if (address) {
        res.redirect('/address/' + address.a_id);
      } else {
        db.get_token(query, function(token) {
          if (token) {
            res.redirect('/token/' +token.t_id);
          } else {
            lib.get_blockhash(query, function(hash) {
              if (hash != 'There was an error. Check your console.') {
                res.redirect('/block/' + hash);
              } else {
                route_get_index(res, locale.ex_search_error + query );
              }
            });
          }
        });
      }
    });
  }
});

router.get('/qr/:string', function(req, res) {
  if (req.param('string')) {
    var address = qr.image(req.param('string'), {
      type: 'png',
      size: 4,
      margin: 1,
      ec_level: 'M'
    });
    res.type('png');
    address.pipe(res);
  }
});

router.get('/ext/stats', function(req, res) {
  // for testnet just get the active addresses and token counts
  if (settings.network == 'testnet') {
    db.count_addresses(function(address_count) {
      db.count_tokens(function(token_count) {
        res.send({ data: [{
          active_address_count: address_count,
          issued_token_count: token_count
        }]});
      });
    });
  // for mainnet get all of the stats, including adding in testnet token and address counts
  } else {
    var wallet_download_count = 0
    var address_count_testnet = 0
    var token_count_testnet = 0
    // grab data from testnet
    request({uri: "https://testnet-explorer.nebl.io/ext/stats", json: true, timeout: 2000, headers: {'User-Agent': 'neblio-block-explorer'}}, function (error, response, body) {
      address_count_testnet = body.data[0].active_address_count
      token_count_testnet = body.data[0].issued_token_count
      // get github download count
      request({uri: "https://api.github.com/repos/NeblioTeam/neblio/releases", json: true, timeout: 2000, headers: {'User-Agent': 'neblio-block-explorer'}}, function (error, response, body) {
        for (var x = 0; x < body.length; x++){
          if (body[x].assets && body[x].assets.length){
            for (var a = 0; a < body[x].assets.length; a++){
              if (body[x].assets[a].download_count && body[x].assets[a].download_count > 0){
                wallet_download_count += body[x].assets[a].download_count
              }
            }
          }
        }
        // get current active node count
        request({uri: "http://localhost:3003/24h_active_node_count", json: true, timeout: 2000, headers: {'User-Agent': 'neblio-block-explorer'}}, function (error, response, node_count) {
          db.count_addresses(function(address_count) {
            db.count_tokens(function(token_count) {
              // add testnet and mainnet to get total counts
              var address_count_total = address_count + address_count_testnet
              var token_count_total = token_count + token_count_testnet

              // process github loc
              var github_lines_of_code = 0
              var gh_loc_path = './data/github_loc.dat'
              // try to get total lines counted
              try {
                if (fs.existsSync(gh_loc_path)) {
                  //file exists
                  try {
                    github_lines_of_code = parseInt(fs.readFileSync(gh_loc_path, 'utf8').trim())
                  } catch (err) {
                    console.error(err)
                  }
                }
              } catch(err) {
                console.error(err)
              }
              res.send({ data: [{
                active_address_count: address_count_total,
                issued_token_count: token_count_total,
                wallet_download_count: wallet_download_count,
                active_node_count: node_count,
                github_lines_of_code: github_lines_of_code,
                active_address_count_mainnet: address_count,
                issued_token_count_mainnet: token_count,
                active_address_count_testnet: address_count_testnet,
                issued_token_count_testnet: token_count_testnet
              }]});
            });
          });
        });
      });
    });
  }
});

router.get('/ext/summary', function(req, res) {
  lib.get_difficulty(function(difficulty) {
    difficultyHybrid = ''
    if (difficulty['proof-of-work']) {
            if (settings.index.difficulty == 'Hybrid') {
              difficultyHybrid = 'POS: ' + difficulty['proof-of-stake'];
              difficulty = 'POW: ' + difficulty['proof-of-work'];
            } else if (settings.index.difficulty == 'POW') {
              difficulty = difficulty['proof-of-work'];
            } else {
        difficulty = difficulty['proof-of-stake'];
      }
    }
    lib.get_hashrate(function(hashrate) {
      lib.get_connectioncount(function(connections){
        lib.get_blockcount(function(blockcount) {
          lib.get_blockhash(blockcount, function(hash1) {
            lib.get_block(hash1, function(block1) {
              lib.get_blockhash(blockcount-2880, function(hash2) {
                lib.get_block(hash2, function(block2) {
                  var blocktime = (block1.time - block2.time)/2880;
                  db.count_addresses(function(address_count) {
                    db.get_stats(settings.coin, function (stats) {
                      if (hashrate == 'There was an error. Check your console.') {
                        hashrate = 0;
                      }
                      res.send({ data: [{
                        difficulty: difficulty,
                        difficultyHybrid: difficultyHybrid,
                        supply: stats.supply,
                        hashrate: hashrate,
                        lastPriceBTC: stats.last_price_btc,
                        lastPriceUSD: stats.last_price_usd,
                        connections: connections,
                        blockcount: blockcount,
                        blocktime: blocktime,
                        address_count: address_count
                      }]});
                    });
                  });
                });
              });
            });
          });
        });
      });
    });
  });
});
module.exports = router;
