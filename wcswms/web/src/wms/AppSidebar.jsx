import React,{useState} from 'react';
import {Box,Download,Upload,ClipboardList,MapPin,Settings,Settings2,ArrowRightLeft,LogOut,UserRound,ChevronDown,ChevronRight,PanelsTopLeft,Warehouse,ChevronsLeft,ChevronsRight} from 'lucide-react';

const groups=[
 {id:'work',label:'工作中心',Icon:PanelsTopLeft,items:[['inbound','入库单',Download],['outbound','出库单',Upload],['transfer','调仓管理',ArrowRightLeft],['tasks','作业任务',ClipboardList]]},
 {id:'stock',label:'库存管理',Icon:Warehouse,items:[['inventory','物料库存',Box],['locations','库位管理',MapPin]]},
 {id:'system',label:'系统管理',Icon:Settings2,items:[['device','设备状态',Settings],['settings','系统设置',Settings2]]}
];

export default function AppSidebar({user,page,onNavigate,onLogout,logoutBusy}){
 const [closed,setClosed]=useState({}),[compact,setCompact]=useState(false);
 const selected=page==='aging'?'transfer':page;
 return <aside className={`wm-sidebar wm-enterprise-sidebar ${compact?'wm-sidebar-compact':''}`}>
  <div className="wm-profile"><button className="wm-profile-account" onClick={()=>onNavigate('settings')} aria-label={`账户设置 ${user.display_name}`}><span className="wm-profile-avatar"><UserRound size={31}/></span><span><strong>{user.display_name}</strong><small>{user.role==='admin'?'系统管理员':user.permissions.length?'仓储操作员':'只读用户'}</small></span></button><div className="wm-profile-actions"><button onClick={onLogout} disabled={logoutBusy}><LogOut size={18}/><span>退出登录</span></button><button aria-label={compact?'展开导航':'收起导航'} onClick={()=>setCompact(value=>!value)}>{compact?<ChevronsRight size={20}/>:<ChevronsLeft size={20}/>}</button></div></div>
  <nav aria-label="仓储导航">{groups.map(({id,label,Icon,items})=><div className="wm-nav-group" key={id}><button className="wm-nav-heading" aria-expanded={!closed[id]} aria-controls={`wm-nav-${id}`} onClick={()=>setClosed(old=>({...old,[id]:!old[id]}))}><Icon size={20}/><span>{label}</span>{closed[id]?<ChevronRight size={15}/>:<ChevronDown size={15}/>}</button><div id={`wm-nav-${id}`} className={`wm-nav-items ${closed[id]?'wm-nav-items-closed':''}`}>{items.map(([itemId,itemLabel,ItemIcon])=><button key={itemId} title={itemLabel} aria-current={selected===itemId?'page':undefined} className={selected===itemId?'active':''} onClick={()=>onNavigate(itemId)}><ItemIcon size={18}/><span>{itemLabel}</span></button>)}</div></div>)}</nav>
  <div className="wm-sidebar-footer"><strong>仓储作业台</strong><span>WMS 1.8.0 · 本地部署</span></div>
 </aside>;
}
