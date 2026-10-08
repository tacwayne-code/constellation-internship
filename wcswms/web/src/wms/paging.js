import {useState,useSyncExternalStore} from 'react';

const listeners=new Set();
const resize=()=>listeners.forEach(listener=>listener());
function subscribe(listener){
 listeners.add(listener);
 if(listeners.size===1)window.addEventListener('resize',resize);
 return()=>{listeners.delete(listener);if(!listeners.size)window.removeEventListener('resize',resize);};
}
const dimensions=()=>`${window.innerWidth}:${window.innerHeight}`;
export function useViewport(){
 const snapshot=useSyncExternalStore(subscribe,dimensions,()=>'1366:768');
 const [width,height]=snapshot.split(':').map(Number);
 return {width,height};
}
export function pageWindow(items,page,size){
 const pageSize=Math.max(1,Math.floor(size));
 const pages=Math.max(1,Math.ceil(items.length/pageSize));
 const current=Math.max(0,Math.min(Math.floor(page)||0,pages-1));
 return {items:items.slice(current*pageSize,(current+1)*pageSize),page:current,pages,total:items.length,size:pageSize};
}
export function usePagination(items,size,resetKey=''){
 const key=`${resetKey}:${size}`;
 const [position,setPosition]=useState({key,page:0});
 return {...pageWindow(items,position.key===key?position.page:0,size),
  setPage:page=>setPosition({key,page})};
}
export function useTableSize(){
 const {width,height}=useViewport();
 return Math.max(3,Math.min(width<760?6:12,Math.floor((height-(width<760?380:330))/64)));
}
