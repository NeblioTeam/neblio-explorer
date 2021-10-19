var mongoose = require('mongoose')
  , Schema = mongoose.Schema;
 
var ProposalSchema = new Schema({
  p_id: { type: Number, default: 0, unique: true, index: true},
  name: { type: String, default: "" },
  desc: { type: String, default: "" },
  url: { type: String, default: "" },
  start_block: { type: Number, default: 0, index: true },
  end_block: { type: Number, default: 0, index: true },
}, {id: false});

module.exports = mongoose.model('Proposal', ProposalSchema);
