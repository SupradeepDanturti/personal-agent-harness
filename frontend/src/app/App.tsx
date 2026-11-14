import { useEffect, useMemo, useRef, useState } from "react";
import { Theme } from "@astryxdesign/core/theme";
import { Button } from "@astryxdesign/core/Button";
import { TextInput } from "@astryxdesign/core/TextInput";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
import { ApiError, createApi, type Item } from "../api/client";
import { txt } from "../lib/format";
import { session, setSession } from "../lib/session";
import { type View } from "./navigation";
import { WorkspaceShell } from "./WorkspaceShell";
import { Chat } from "../features/chat/Chat";
import { Memory } from "../features/memory/Memory";
import { Goals } from "../features/goals/Goals";
import { Schedules } from "../features/schedules/Schedules";
import { Workflows } from "../features/workflows/Workflows";
import { Tasks } from "../features/tasks/Tasks";
import { Browser } from "../features/browser/Browser";
import { Approvals } from "../features/approvals/Approvals";
import { Inbox } from "../features/inbox/Inbox";
import { Files } from "../features/files/Files";
import { Connections } from "../features/connections/Connections";
import { Activity } from "../features/activity/Activity";
import { HistoryExplorer } from "../features/history/HistoryExplorer";
import { People } from "../features/people/People";
import { Repairs } from "../features/repairs/Repairs";
import { Evidence } from "../features/evidence/Evidence";

export function App() {
  const [mode,setMode]=useState<"light"|"dark">(()=>document.documentElement.dataset.theme==="light"?"light":"dark");
  const [view,setView]=useState<View>("Chat");
  const [token,setToken]=useState("");
  const tokenRef=useRef("");
  const generation=useRef(0);
  const connectLock=useRef(false);
  const bootStarted=useRef(false);
  const [connecting,setConnecting]=useState(false);
  const [connectionError,setConnectionError]=useState("");
  const [draft,setDraft]=useState("");
  const [runtime,setRuntime]=useState<Item>({});
  const api=useMemo(()=>{
    const apiGeneration=generation.current;
    return createApi(token,()=>{
      if(tokenRef.current!==token||generation.current!==apiGeneration)return;
      generation.current+=1;tokenRef.current="";
      setSession("harness-token","");setToken("");setRuntime({});
      setConnectionError("Workspace token was rejected or changed. Reconnect your workspace.");
    });
  },[token]);
  useEffect(()=>{
    try{localStorage.setItem("harness-color-mode",mode);}catch{}
    document.documentElement.dataset.theme=mode;
    document.documentElement.style.colorScheme=mode;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content",mode==="dark"?"#191919":"#f6f5f3");
  },[mode]);
  const verifyConnection=async(candidate:string)=>{
    if(!candidate||connectLock.current)return;
    connectLock.current=true;setConnecting(true);setConnectionError("");
    const attempt=++generation.current;
    try{
      const status=await createApi(candidate)("/status");
      if(attempt!==generation.current)return;
      tokenRef.current=candidate;setSession("harness-token",candidate);
      setRuntime(status);setToken(candidate);setDraft("");
    }catch(cause){
      if(attempt!==generation.current)return;
      if(cause instanceof ApiError&&cause.status===401)setSession("harness-token","");
      else setDraft(candidate);
      setConnectionError(cause instanceof ApiError?cause.message:"Cannot reach your assistant. Check that the local server is running, then try again.");
    }finally{connectLock.current=false;setConnecting(false);}
  };
  useEffect(()=>{
    if(bootStarted.current)return;bootStarted.current=true;
    void verifyConnection(session("harness-token"));
  },[]);
  const connect=()=>{void verifyConnection(draft.trim());};
  const disconnect=()=>{
    generation.current+=1;tokenRef.current="";setSession("harness-token","");
    setToken("");setRuntime({});setConnectionError("");
  };
  return <Theme theme={neutralTheme} mode={mode}>
    {!token?<main className="login-shell">
      <a className="login-brand" href="#" aria-label="Personal Harness">&#10035; <span>Personal Harness</span></a>
      <section className="connect-panel">
        <div className="connect-art" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><span>&#10035;</span></div>
        <div>
          <div className="eyebrow">YOUR PERSONAL ASSISTANT</div>
          <h1>A little context.<br/>A clear next step.</h1>
          <p>Conversations, useful memory, and work that keeps moving.<br/>Connect to your private workspace to pick up where you left off.</p>
          <form className="connect-form" onSubmit={event=>{event.preventDefault();connect();}}>
            {connectionError&&<div className="error" role="alert">{connectionError}</div>}
            <TextInput label="Workspace access token" type="password" value={draft} isDisabled={connecting} onChange={setDraft} placeholder="Enter your private access token"/>
            <Button label="Connect workspace" variant="primary" isDisabled={connecting||!draft.trim()} onClick={connect}/>
            {connecting&&<span role="status">Connecting…</span>}
          </form>
          <small>Your token stays in this tab's session. Disconnect to remove it.</small>
        </div>
      </section>
      <span className="login-footer">Your workspace. Your context. Your control.</span>
    </main>:<WorkspaceShell api={api} runtime={runtime} view={view} onNavigate={setView} mode={mode} onModeChange={setMode} onDisconnect={disconnect}>
      {view==="Chat"&&<Chat api={api} model={txt(runtime.model)||"GPT-6 Luna"}/>}
      <div className={view==="Chat"?"workspace-hidden":"workspace-page-content"}>
        {view==="Goals"&&<Goals api={api}/>}
        {view==="Memory"&&<Memory api={api}/>}
        {view==="History"&&<HistoryExplorer api={api}/>}
        {view==="People"&&<People api={api}/>}
        {view==="Repairs"&&<Repairs api={api}/>}
        {view==="Evidence"&&<Evidence api={api}/>}
        {view==="Schedules"&&<Schedules api={api}/>}
        {view==="Tasks"&&<Tasks api={api} navigate={setView}/>}
        {view==="Workflows"&&<Workflows api={api}/>}
        {view==="Browser"&&<Browser api={api} token={token} navigate={setView}/>}
        {view==="Approvals"&&<Approvals api={api}/>}
        {view==="Inbox"&&<Inbox api={api} navigate={setView}/>}
        {view==="Files"&&<Files api={api}/>}
        {view==="Connections"&&<Connections api={api} runtime={runtime} navigate={setView}/>}
        {view==="Activity"&&<Activity api={api}/>}
      </div>
    </WorkspaceShell>}
  </Theme>;
}
