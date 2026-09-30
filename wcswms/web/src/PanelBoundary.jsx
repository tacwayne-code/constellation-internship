import React from 'react';
import {RefreshCw,TriangleAlert} from 'lucide-react';

export default class PanelBoundary extends React.Component {
 state={error:null};
 static getDerivedStateFromError(error){return {error};}
 render(){
  if(!this.state.error)return this.props.children;
  return <section className="panel component-error" role="alert"><TriangleAlert size={23}/><div><h3>{this.props.name}暂时无法显示</h3><p>该区域渲染出现异常，其他页面功能仍可使用。</p><code>{this.state.error.message||'组件加载失败'}</code><button className="compact" onClick={()=>this.setState({error:null})}><RefreshCw size={14}/>重新加载该区域</button></div></section>;
 }
}
