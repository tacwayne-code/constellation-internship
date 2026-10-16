import test from 'node:test';
import assert from 'node:assert/strict';
import {boxContents,materialRows} from '../web/src/wms/material.js';
import {findMaterials,materialInventory} from '../web/src/wms/materialSearch.js';
const catalog=[{sku:'0006204',material_name:'深沟球轴承',model:'BR-6204',specification:'20 mm'},{sku:'BEARING-2',material_name:'深沟球轴承',model:'BR-6205',specification:'30 mm'},{sku:'MOTOR',material_name:'电机',model:'BR',specification:'20 mm'}];

test('mixed box totals and outbound rows retain each SKU quantity and barcode',()=>{
 const mixed={barcode:'MIX',sku:'A',quantity:12,status:'IN_STOCK',container_type:'MATERIAL',location:'L-01-01-1',contents:[{sku:'A',quantity:5,material_name:'甲'},{sku:'B',quantity:7,material_name:'乙'}]};
 assert.equal(boxContents(mixed).length,2);
 assert.deepEqual(materialRows([mixed]).map(row=>[row.barcode,row.sku,row.quantity]),[['MIX','A',5],['MIX','B',7]]);
 assert.deepEqual(materialInventory([mixed]).map(row=>[row.sku,row.quantity,row.boxes.length]),[['A',5,1],['B',7,1]]);
 assert.deepEqual(materialInventory([{...mixed,status:'SHIPPED'}]),[]);
});
test('material suggestions match code, name, model and specification with multiple terms',()=>{
 assert.deepEqual(findMaterials(catalog,'轴承 30').map(row=>row.sku),['BEARING-2']);
 assert.deepEqual(findMaterials(catalog,'br-6204').map(row=>row.sku),['0006204']);
 assert.equal(findMaterials(catalog,'０００６２０４')[0].sku,'0006204');
 assert.deepEqual(findMaterials(catalog,'轴承 30 mm').map(row=>row.sku),['BEARING-2']);
 assert.deepEqual(findMaterials(catalog,''),[]);
 assert.deepEqual(findMaterials(catalog,'UNKNOWN'),[]);
});
test('stock totals group by material code, retain working/reserved details and omit empty or closed boxes',()=>{
 const stock=[{sku:'A',quantity:5,status:'IN_STOCK',barcode:'ONE',reserved:'TASK'},{sku:'A',quantity:3,status:'AT_STATION',barcode:'TWO'},{sku:'A',quantity:2,status:'AT_EXIT',barcode:'THREE'},{sku:'A',quantity:99,status:'SHIPPED'},{sku:'A',quantity:99,status:'REMOVED'},{sku:'',quantity:0,status:'IN_STOCK',container_type:'EMPTY_BIN'},{sku:'B',quantity:7,status:'IN_STOCK',barcode:'OTHER'}];
 const groups=materialInventory(stock);
 assert.equal(groups.length,2);
 assert.equal(groups[0].quantity,10);
 assert.equal(groups[0].inStock,5);
 assert.equal(groups[0].working,5);
 assert.equal(groups[0].reserved,1);
 assert.deepEqual(groups[0].boxes.map(box=>box.barcode),['ONE','TWO','THREE']);
 assert.equal(stock[0].quantity,5);
});
