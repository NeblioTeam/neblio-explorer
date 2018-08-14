var mongoose = require('mongoose')
  , Schema = mongoose.Schema;
 
var TokenSchema = new Schema({
  t_id: { type: String, unique: true, index: true},
  name: { type: String },
  divisibility: { type: Number, default: 0 },
  lock_status: { type: Boolean, default: true },
  aggregation_policy: { type: String },
  total_supply: { type: Number, default: 0 },
  num_holders: { type: Number, default: 0 },
  num_transfers: { type: Number, default: 0 },
  num_issuance: { type: Number, default: 0 },
  num_burns: { type: Number, default: 0 },
  first_block: { type: Number, default: 0 },
  // issuance_txid: { type: String },
  // issuance_addr: { type: String },

  issuance: { type: Object, default: {} },
}, {id: false});

module.exports = mongoose.model('Token', TokenSchema);
