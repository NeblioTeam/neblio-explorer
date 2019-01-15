var request = require('request');

var base_url = 'https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?';
function get_summary(coin, exchange, cmc_api_key, cb) {
    var summary = {};
    request({ uri: base_url + 'symbol=NEBL&convert=BTC', json: true, headers: {'X-CMC_PRO_API_KEY': cmc_api_key}, }, function (error, response, body) {
        if (error) {
            return cb(error, null);
        } else if (body.data.NEBL) {
            summary['btc'] = body.data.NEBL.quote.BTC.price.toFixed(8);
            request({ uri: base_url + 'symbol=NEBL&convert=USD', json: true, headers: {'X-CMC_PRO_API_KEY': cmc_api_key}, }, function (error, response, body) {
		        if (error) {
		            return cb(error, null);
		        } else if (body.data.NEBL) {
		            summary['usd'] = body.data.NEBL.quote.USD.price.toFixed(2);
		            return cb(null, summary);
		        } else {
		            return cb(error, null);
		        }
		    });
        } else {
            return cb(error, null);
        }
    });
}

module.exports = {
    get_data: function (coin, exchange, cmc_api_key, cb) {
        var error = null;
        get_summary(coin, exchange, cmc_api_key, function (err, stats) {
            if (err) { error = err; }
            return cb(error, { buys: [], sells: [], chartdata: [], trades: [], stats: stats });
        });
    }
};

