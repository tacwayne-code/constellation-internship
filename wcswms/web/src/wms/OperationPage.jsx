import React,{useState} from 'react';
import ScanPage from './ScanPage';
import MaterialOrderPage from './MaterialOrderPage';

export default function OperationPage(props){
 const [mode,setMode]=useState('material');
 return <><div className="wm-tabs wm-section-tools" aria-label="作业类型"><button className={mode==='material'?'selected':''} onClick={()=>setMode('material')}>物料{props.kind==='INBOUND'?'入库':'出库'}单</button><button className={mode==='empty'?'selected':''} onClick={()=>setMode('empty')}>空箱{props.kind==='INBOUND'?'入库':'出库'}</button></div>{mode==='material'?<MaterialOrderPage {...props}/>:<ScanPage {...props} emptyOnly/>}</>;
}
