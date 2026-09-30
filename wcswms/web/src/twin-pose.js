const axes=['x','y','z'];
export function validPhysicalCalibration(value){
 return !!value&&value.version===1&&value.mode==='FIXED_ANCHOR'
  &&axes.every(axis=>Number.isFinite(value.plc_anchor_mm?.[axis])
   &&Number.isFinite(value.cad_anchor_m?.[axis])&&[-1,1].includes(value.axis_sign?.[axis]))
  &&Number.isFinite(value.fork_mid_ratio)&&value.fork_mid_ratio>=0&&value.fork_mid_ratio<=1;
}

// The fixed survey point is independent of the server's startup sample.
// A physical pose must never use the simulator's illustrative affine mapping.
export function physicalPreviewPose(state, reference, calibration){
 if(calibration){
  if(!validPhysicalCalibration(calibration))return null;
  const positions=Object.fromEntries((state?.axes||[]).map(item=>[item.axis,item.value]));
  if(!axes.every(axis=>Number.isFinite(positions[axis])))return null;
  const mapped=Object.fromEntries(axes.map(axis=>[axis,calibration.cad_anchor_m[axis]
   +calibration.axis_sign[axis]*(positions[axis]-calibration.plc_anchor_mm[axis])/1000]));
  return {x:mapped.x,y:mapped.y,dx:mapped.x-reference.travel_x,dy:mapped.y-reference.lift_y,
   forkMid:mapped.z*calibration.fork_mid_ratio-reference.fork_mid_extension,
   forkTip:mapped.z-reference.fork_tip_extension};
 }
 const delta=state?.twin?.relative_m;
 if(!delta||!axes.every(axis=>Number.isFinite(delta[axis])))return null;
 return {x:reference.travel_x+delta.x,y:reference.lift_y+delta.y,
  dx:delta.x,dy:delta.y,forkMid:delta.z/2,forkTip:delta.z};
}
