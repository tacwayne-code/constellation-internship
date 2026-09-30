export const materialFields=[['material_name','物料名称',120],['model','型号',120],['specification','规格',200]];
export const materialDetails=item=>Object.fromEntries(materialFields.map(([key])=>[key,item?.[key]||'']));
