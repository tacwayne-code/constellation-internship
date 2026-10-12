import React,{useState} from 'react';
import {Box,LogIn,ShieldCheck} from 'lucide-react';
import {api,clearSession,setSession} from './api';

export default function LoginPage({info,onLogin,notice,onRetry}){
 const setup=info?.initialized===false;
 const [username,setUsername]=useState(''),[name,setName]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState(''),[key,setKey]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(event){
  event.preventDefault();if(busy||!info)return;
  if(setup&&password!==confirm){setError('两次输入的密码不一致');return;}
  setBusy(true);setError('');
  try{const session=await api(setup?'/api/auth/bootstrap':'/api/auth/login',setup?{username,display_name:name,password,control_key:key}:{username,password});clearSession();setSession(session);onLogin(session);}
  catch(e){setError(e.message);}finally{setBusy(false);}
 }
 return <main className={`wm-login ${setup?'wm-login-setup':''}`}><section className="wm-login-brand"><Box size={32}/><strong>仓储作业台</strong><span>WMS · 现场作业</span></section><section className="wm-login-panel"><div className="wm-login-heading">{setup?<ShieldCheck size={26}/>:<LogIn size={26}/>}<div><h1>{setup?'创建首位管理员':'用户登录'}</h1><p>{setup?'完成初始化后，即可添加用户并分配权限。':'使用你的账号进入仓储工作台。'}</p></div></div>{!info?<><p className="wm-help">{notice||'正在连接后台…'}</p>{notice&&<button className="wm-primary wm-full" onClick={onRetry}>重新连接</button>}</>:<form onSubmit={submit}><label>用户名<input autoFocus value={username} onChange={e=>setUsername(e.target.value)} minLength={setup?3:1} maxLength={32} required autoComplete="username" placeholder={setup?'例如 admin':'请输入用户名'}/></label>{setup&&<label>显示名称<input value={name} onChange={e=>setName(e.target.value)} maxLength={40} required placeholder="例如 系统管理员"/></label>}<label>{setup?'设置密码':'密码'}<input type="password" value={password} onChange={e=>setPassword(e.target.value)} minLength={setup?10:1} maxLength={128} required autoComplete={setup?'new-password':'current-password'} placeholder={setup?'至少 10 个字符':'请输入密码'}/></label>{setup&&<><label>确认密码<input type="password" value={confirm} onChange={e=>setConfirm(e.target.value)} minLength={10} maxLength={128} required autoComplete="new-password"/></label><label>后台控制密钥<input type="password" value={key} onChange={e=>setKey(e.target.value)} maxLength={256} required autoComplete="off"/></label><p className="wm-help">由后台主机管理员提供，仅用于首次初始化。</p></>}{(error||notice)&&<p className={error?'wm-error':'wm-login-notice'} role={error?'alert':'status'}>{error||notice}</p>}<button className="wm-primary wm-full" disabled={busy}>{busy?'验证中…':setup?'创建管理员并登录':'登录'}</button></form>}</section><p className="wm-login-footer">账号与权限由管理员统一管理</p></main>;
}
