import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {materialDetails} from './material';

// Only the latest code may fill the form, including when a scan is replaced mid-request.
export default function useMaterialLookup(setForm){
 const timer=useRef(null),request=useRef(null),code=useRef('');
 const [status,setStatus]=useState({pending:false,error:false,message:''});
 function cancel(){clearTimeout(timer.current);request.current?.controller.abort();request.current=null;}
 useEffect(()=>()=>cancel(),[]);
 function accept(sku,material){
  cancel();code.current=sku;
  setForm(previous=>({...previous,sku,...materialDetails(material)}));
  setStatus({pending:false,error:false,message:sku?(material?'已识别物料档案，名称、型号、规格已自动填入':'新物料编码，请填写资料；登记后将长期保留'):''});
 }
 async function lookup(value=code.current){
  cancel();const sku=value.trim();code.current=value;
  if(!sku){accept('',null);return;}
  const current={sku,controller:new AbortController()};request.current=current;
  setStatus({pending:true,error:false,message:'正在查询物料档案…'});
  try{
   const result=await api(`/api/materials/lookup?sku=${encodeURIComponent(sku)}`,undefined,{signal:current.controller.signal});
   if(request.current!==current)return;
   setForm(previous=>previous.sku.trim()===sku?{...previous,...materialDetails(result.material)}:previous);
   setStatus({pending:false,error:false,message:result.material?'已识别物料档案，名称、型号、规格已自动填入':'新物料编码，请填写资料；登记后将长期保留'});
  }catch(error){
   if(request.current===current&&error.name!=='AbortError')setStatus({pending:false,error:true,message:'物料档案查询失败，请在编码栏按回车重试'});
  }finally{if(request.current===current)request.current=null;}
 }
 function change(value){
  cancel();code.current=value;
  setForm(previous=>({...previous,sku:value,...materialDetails()}));
  setStatus({pending:Boolean(value.trim()),error:false,message:value.trim()?'正在查询物料档案…':''});
  if(value.trim())timer.current=setTimeout(()=>lookup(value),250);
 }
 return {...status,change,lookup,accept};
}
