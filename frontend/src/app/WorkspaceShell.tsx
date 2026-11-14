import { useEffect, useRef, useState, type ReactNode } from "react";
import { type Api, type Item } from "../api/client";
import { Icon } from "../components/Icon";
import { CompanionPanel } from "../features/activity/CompanionPanel";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { descriptions, type View } from "./navigation";

const primary: {view:View;icon:string}[] = [
  {view:"Chat",icon:"chat"},{view:"History",icon:"search"},{view:"Memory",icon:"memory"},
  {view:"Goals",icon:"goals"},{view:"Tasks",icon:"tasks"},{view:"Workflows",icon:"workflows"},
  {view:"Activity",icon:"activity"},{view:"Connections",icon:"connections"},
];
const secondary: View[] = ["Inbox","Approvals","Schedules","Browser","Files","People","Repairs","Evidence"];

export function WorkspaceShell({api,runtime,view,onNavigate,mode,onModeChange,onDisconnect,children}: {
  api:Api;runtime:Item;view:View;onNavigate:(view:View)=>void;mode:"light"|"dark";
  onModeChange:(mode:"light"|"dark")=>void;onDisconnect:()=>void;children:ReactNode;
}) {
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [moreOpen,setMoreOpen]=useState(false);
  const [panelOpen,setPanelOpen]=useState(()=>window.matchMedia("(min-width: 1180px)").matches);
  const more=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    const query=window.matchMedia("(min-width: 1180px)");
    const change=()=>setPanelOpen(query.matches);
    query.addEventListener("change",change);
    return()=>query.removeEventListener("change",change);
  },[]);
  useEffect(()=>{
    if(!moreOpen)return;
    const click=(event:PointerEvent)=>{if(!more.current?.contains(event.target as Node))setMoreOpen(false);};
    const key=(event:KeyboardEvent)=>{if(event.key==="Escape")setMoreOpen(false);};
    document.addEventListener("pointerdown",click);document.addEventListener("keydown",key);
    return()=>{document.removeEventListener("pointerdown",click);document.removeEventListener("keydown",key);};
  },[moreOpen]);
  const navigate=(next:View)=>{setMoreOpen(false);onNavigate(next);};
  return <div className={`workspace-shell ${view==="Chat"?"workspace-chat":"workspace-page-view"}`}>
    <aside className="workspace-rail">
      <button className="rail-brand" aria-label="Personal Harness home" title="Personal Harness" onClick={()=>navigate("Chat")}><Icon name="sparkles" size={27}/></button>
      <nav aria-label="Main navigation" className="rail-navigation">
        {primary.map(item=><button key={item.view} className={`rail-button ${view===item.view?"active":""}`} aria-label={item.view} title={item.view} aria-current={view===item.view?"page":undefined} onClick={()=>navigate(item.view)}><Icon name={item.icon}/></button>)}
        <div ref={more} className="rail-more">
          <button className={`rail-button ${secondary.includes(view)?"active":""}`} aria-label="More tools" title="More tools" aria-expanded={moreOpen} onClick={()=>setMoreOpen(!moreOpen)}><Icon name="grid"/></button>
          {moreOpen&&<div className="rail-popover" aria-label="More tools navigation"><span className="rail-popover-heading">Workspace tools</span>{secondary.map(name=><button key={name} aria-current={view===name?"page":undefined} onClick={()=>navigate(name)}>{name}<Icon name="arrow-right" size={15}/></button>)}</div>}
        </div>
      </nav>
      <div className="rail-bottom">
        <button className="rail-button" title="Settings" aria-label="Settings" onClick={()=>{setMoreOpen(false);setSettingsOpen(true);}}><Icon name="settings"/></button>
        <button className="rail-button" title="Disconnect workspace" aria-label="Disconnect workspace" onClick={onDisconnect}><Icon name="logout"/></button>
      </div>
    </aside>
    <main className="workspace-main">
      {view!=="Chat"&&<header className="workspace-page-heading"><div><span className="workspace-kicker">Your workspace</span><h1>{view}</h1><p>{descriptions[view]}</p></div><button className="workspace-back" onClick={()=>navigate("Chat")}><Icon name="chat" size={17}/>Back to chat</button></header>}
      <div className={`workspace-stage ${view==="Chat"&&panelOpen?"with-companion":""}`}>
        <div className="workspace-route">
          {view==="Chat"&&<div className="workspace-chat-actions"><span className="runtime-chip"><span/>Local workspace</span><button aria-label={panelOpen?"Hide activity panel":"Show activity panel"} title="Assistant activity" onClick={()=>setPanelOpen(!panelOpen)}><Icon name="panel" size={19}/></button></div>}
          {children}
        </div>
        {view==="Chat"&&panelOpen&&<CompanionPanel api={api} runtime={runtime} onNavigate={navigate} onClose={()=>setPanelOpen(false)}/>}
      </div>
    </main>
    <SettingsDialog api={api} open={settingsOpen} onClose={()=>setSettingsOpen(false)} mode={mode} onModeChange={onModeChange} onNavigate={navigate} onDisconnect={onDisconnect}/>
  </div>;
}
