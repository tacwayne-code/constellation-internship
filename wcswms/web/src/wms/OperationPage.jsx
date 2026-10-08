import React,{useState} from 'react';
import ScanPage from './ScanPage';
import MaterialOrderPage from './MaterialOrderPage';

export default function OperationPage(props){
 const [mode,setMode]=useState('material'),[pane,setPane]=useState('registration');
 return <div className="wm-operation" data-pane={pane}><div className="wm-operation-toolbar"><div className="wm-tabs" aria-label="作业类型"><button className={mode==='material'?'selected':''} onClick={()=>{setMode('material');setPane('registration');}}>物料{props.kind==='INBOUND'?'入库':'出库'}单</button><button className={mode==='empty'?'selected':''} onClick={()=>{setMode('empty');setPane('registration');}}>空箱{props.kind==='INBOUND'?'入库':'出库'}</button></div><button className="wm-mobile-toggle" onClick={()=>setPane(pane==='registration'?'pending':'registration')}>{pane==='registration'?'查看待办':'返回登记'}</button></div>{mode==='material'?<MaterialOrderPage {...props}/>:<ScanPage {...props} emptyOnly/>}</div>;
}
