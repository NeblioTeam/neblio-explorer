var mongoose = require('mongoose')
  , Schema = mongoose.Schema;
 
var VoteSchema = new Schema({
  block_height: { type: Number, default: 0, unique: true, index: true},
  block_hash: { type: String, default: "", unique: true, index: true},
  proposal_id: { type: Number, default: 0, index: true},
  vote_value: { type: String, default: ""},
  staker_addr: { type: String, default: "", index: true},
}, {id: false});

module.exports = mongoose.model('Vote', VoteSchema);
