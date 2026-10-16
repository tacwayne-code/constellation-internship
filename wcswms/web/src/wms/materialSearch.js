import {materialRows} from './material.js';
const normalize=value=>String(value||'').normalize('NFKC').toLowerCase().trim();
export function findMaterials(materials,query){
 const text=normalize(query),terms=text.split(/\s+/).filter(Boolean);
 if(!terms.length)return [];
 const rank=item=>normalize(item.sku)===text?0:normalize(item.sku).startsWith(text)?1:2;
 return materials.filter(item=>{const haystack=[item.sku,item.material_name,item.model,item.specification].map(normalize).join(' ');return terms.every(term=>haystack.includes(term));}).sort((a,b)=>rank(a)-rank(b)||String(a.sku).localeCompare(String(b.sku)));
}
export function materialInventory(stock){
 const groups=new Map();
 for(const box of materialRows(stock)){
  if(box.container_type==='EMPTY_BIN'||!box.sku||!['IN_STOCK','AT_STATION','AT_EXIT'].includes(box.status))continue;
  let item=groups.get(box.sku);
  if(!item){item={sku:box.sku,material_name:box.material_name||'',model:box.model||'',specification:box.specification||'',quantity:0,inStock:0,working:0,reserved:0,boxes:[]};groups.set(box.sku,item);}
  const quantity=Number(box.quantity)||0;
  item.quantity+=quantity;
  if(box.status==='IN_STOCK')item.inStock+=quantity;else item.working+=quantity;
  if(box.reserved)item.reserved++;
  item.boxes.push(box);
 }
 return [...groups.values()].sort((a,b)=>a.sku.localeCompare(b.sku));
}
