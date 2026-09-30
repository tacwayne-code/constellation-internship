import React from 'react';
import RealTwin from './RealTwin.jsx';

export default function CadView({url, state, onSelect}) {
  return <section className="panel cad-view"><RealTwin url={url} state={state} onSelect={onSelect}/></section>;
}
