export const materialFields=[['material_name','物料名称',120],['model','型号',120],['specification','规格',200]];
export const materialDetails=item=>Object.fromEntries(materialFields.map(([key])=>[key,item?.[key]||'']));
export const boxContents=item=>Array.isArray(item?.contents)?item.contents:item?.container_type!=='EMPTY_BIN'&&item?.sku&&Number(item.quantity)>0?[{sku:item.sku,quantity:item.quantity,batch:item.batch||'',...materialDetails(item)}]:[];
export const materialRows=stock=>stock.flatMap(box=>boxContents(box).map(line=>({...box,...line,box_quantity:box.quantity,contents:boxContents(box)})));
export const orderLines=order=>order.kind==='COUNT'&&Array.isArray(order.lines)?order.lines:order.lines?.length?order.lines:[order];
