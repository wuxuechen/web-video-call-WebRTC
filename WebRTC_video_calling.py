#!/usr/bin/env python3
"""
Python WebRTC signaling + static page (single file).
- Serves an HTML page that opens your camera/mic and joins a room.
- Python provides ONLY signaling via WebSocket; media is P2P over WebRTC.
- Two people open the same room name to video call each other.

Run:
  pip install aiohttp==3.*
  python webrtc_singlefile.py
Then open http://127.0.0.1:8080/ in two browsers/devices, enter the same Room ID, and click "Join".

Security/Notes:
- This is a demo. For production, add HTTPS (TLS), auth, TURN servers, and harden the signaling.
- For NAT traversal add public STUN/TURN in ICE_SERVERS below (at least STUN is included).
"""
import asyncio
import json
import os
from pathlib import Path
from aiohttp import web

# ------------------------------ HTML/JS client ------------------------------
INDEX_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Python WebRTC Demo</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#0b1020;color:#e7e9ee}
    .wrap{max-width:980px;margin:0 auto;padding:24px}
    h1{font-size:22px;margin:0 0 12px}
    .card{background:#121830;border:1px solid #1f2744;border-radius:16px;padding:16px;box-shadow:0 4px 24px rgba(0,0,0,.25)}
    label{display:block;font-size:12px;color:#9aa4bf;margin-bottom:6px}
    input,button{font:inherit;border-radius:10px;border:1px solid #2a355d;background:#0d1530;color:#e7e9ee;padding:10px}
    input{width:100%;}
    button{cursor:pointer}
    .row{display:flex;gap:12px;align-items:center}
    .row>div{flex:1}
    video{width:100%;max-height:42vh;background:#000;border-radius:12px}
    .videos{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .hint{font-size:12px;color:#9aa4bf}
    .pill{display:inline-block;padding:4px 8px;border:1px solid #2a355d;border-radius:999px;margin-right:6px}
    .log{white-space:pre-wrap;background:#0a0f22;border-radius:12px;border:1px dashed #2a355d;padding:10px;max-height:160px;overflow:auto}
  </style>
</head>
<body>
<div class="wrap">
  <h1>WebRTC 视频通话 (Python 信令)</h1>
  <div class="card" style="margin-bottom:12px">
    <div class="row">
      <div>
        <label>Room ID</label>
        <input id="room" placeholder="比如: test123" />
      </div>
      <div style="flex:0 0 auto;display:flex;gap:8px;align-items:end">
        <button id="join">Join</button>
        <button id="leave" disabled>Leave</button>
      </div>
    </div>
    <div class="hint" style="margin-top:8px">在两台设备上输入相同的 Room ID 并点击 Join。</div>
    <div style="margin-top:8px">
      <span class="pill" id="status">Idle</span>
      <span class="pill" id="ice"></span>
    </div>
  </div>

  <div class="videos card">
    <div>
      <label>本地视频</label>
      <video id="local" autoplay playsinline muted></video>
    </div>
    <div>
      <label>远端视频</label>
      <video id="remote" autoplay playsinline></video>
    </div>
  </div>

  <div class="card" style="margin-top:12px">
    <label>日志</label>
    <div id="log" class="log"></div>
  </div>
</div>
<script>
const ICE_SERVERS = [
  { urls: 'stun:stun.l.google.com:19302' },
  // ⬇️ 如有自己的 TURN，在这里加上（强烈建议，跨网/4G 必备）
  // { urls: 'turn:your.turn.server:3478', username: 'user', credential: 'pass' },
];

let pc, ws, localStream;
let roomId = '';

function log(msg){
  const el = document.getElementById('log');
  el.textContent += `\n${new Date().toISOString().slice(11,19)} ${msg}`;
  el.scrollTop = el.scrollHeight;
}

function setStatus(s){ document.getElementById('status').textContent = s; }
function setIce(s){ document.getElementById('ice').textContent = s; }

async function startLocal(){
  try{
    localStream = await navigator.mediaDevices.getUserMedia({video:true,audio:true});
    document.getElementById('local').srcObject = localStream;
    log('got local media');
  }catch(e){
    log('getUserMedia error: '+ e.name + ' ' + e.message);
    throw e;
  }
}

async function join(){
  roomId = document.getElementById('room').value.trim();
  if(!roomId){ alert('请输入 Room ID'); return; }

  await startLocal();

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws?room=${encodeURIComponent(roomId)}`);
  ws.onopen = ()=>{ log('WS connected'); };
  ws.onmessage = async (ev)=>{
    const msg = JSON.parse(ev.data);
    if(msg.type==='offer'){
      await ensurePC();
      log('recv OFFER');
      await pc.setRemoteDescription(msg.sdp);
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      ws.send(JSON.stringify({type:'answer', sdp: pc.localDescription}));
      log('sent ANSWER');
    } else if(msg.type==='answer'){
      log('recv ANSWER');
      await pc.setRemoteDescription(msg.sdp);
    } else if(msg.type==='ice'){
      if(msg.candidate){
        try {
          await pc.addIceCandidate(msg.candidate);
        } catch(e){
          log('addIceCandidate error: '+e);
        }
      }
    } else if(msg.type==='joined'){
      log(`joined, peers=${msg.count}`);
      // 只有后加入者（房间里已有其他人）发起 offer
      if(msg.count > 1){
        await ensurePC();
        const offer = await pc.createOffer({offerToReceiveAudio:true, offerToReceiveVideo:true});
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({type:'offer', sdp: pc.localDescription}));
        log('sent OFFER');
      }
    }
  };
  ws.onclose = ()=>{ log('WS closed'); cleanup(); };

  document.getElementById('join').disabled = true;
  document.getElementById('leave').disabled = false;
  setStatus('Joined: '+roomId);
}

async function ensurePC(){
  if(pc) return;
  pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
  setIce('gathering…');

  pc.onicegatheringstatechange = ()=> setIce(pc.iceGatheringState);
  pc.oniceconnectionstatechange = ()=>{
    log('iceConnectionState: ' + pc.iceConnectionState);
    if(pc.iceConnectionState === 'failed'){
      log('ICE failed — 需要 TURN 才能打通（跨网/蜂窝时尤为常见）');
    }
  };
  pc.onconnectionstatechange = ()=>{
    log('connectionState: ' + pc.connectionState);
    setStatus(pc.connectionState);
    if(pc.connectionstate === 'connected'){ dumpSelectedCandidate(); }
  };
  pc.onicecandidateerror = (e)=>{
    log(`icecandidateerror: addr=${e.address||''} port=${e.port||''} url=${e.url||''} errorCode=${e.errorCode||''}`);
  };

  // 本地流加入
  localStream.getTracks().forEach(t=> pc.addTrack(t, localStream));

  pc.onicecandidate = (ev)=>{
    if(ev.candidate){ ws?.send(JSON.stringify({type:'ice', candidate:ev.candidate})); }
    else { log('ICE gathering complete'); }
  };

  // 接收远端流
  pc.ontrack = (ev)=>{
    log('got REMOTE track');
    const remoteVideo = document.getElementById('remote');
    if(!remoteVideo.srcObject){
      remoteVideo.srcObject = ev.streams[0];
    }
  };
}

// 打印当前选中的候选对（方便判断是否真连上）
async function dumpSelectedCandidate(){
  try{
    const stats = await pc.getStats();
    stats.forEach(report=>{
      if(report.type === 'candidate-pair' && report.state === 'succeeded' && report.selected){
        log(`Selected pair: local=${report.localCandidateId} <-> remote=${report.remoteCandidateId}`);
      }
    });
  }catch(_){}
}

function leave(){
  ws?.close();
  cleanup();
}

function cleanup(){
  document.getElementById('join').disabled = false;
  document.getElementById('leave').disabled = true;
  if(pc){ pc.getSenders().forEach(s=>s.track && s.track.stop()); pc.close(); pc=null; }
  if(localStream){ localStream.getTracks().forEach(t=>t.stop()); localStream=null; document.getElementById('local').srcObject=null; }
  document.getElementById('remote').srcObject=null;
  setStatus('Idle'); setIce('');
}

document.getElementById('join').onclick = join;
addEventListener('beforeunload', leave);
document.getElementById('leave').onclick = leave;
</script>
</body>
</html>
"""

# ------------------------------ WebSocket signaling ------------------------------
rooms = {}  # room_id -> set of WebSocketResponse

async def ws_handler(request: web.Request):
    room_id = request.query.get('room', 'default')
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)

    peers = rooms.setdefault(room_id, set())
    peers.add(ws)
    # Notify join + peer count
    await ws.send_json({"type": "joined", "count": len(peers)})
    for p in list(peers):
        if p is not ws:
            try:
                await p.send_json({"type":"peers", "count": len(peers)})
            except Exception:
                pass

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                payload = json.loads(msg.data)
                # Relay to other peers in the room
                for p in list(peers):
                    if p is not ws:
                        await p.send_json(payload)
            elif msg.type == web.WSMsgType.ERROR:
                print('WS connection closed with exception %s' % ws.exception())
    finally:
        # Cleanup
        peers.discard(ws)
        if not peers:
            rooms.pop(room_id, None)
    return ws

# ------------------------------ HTTP server ------------------------------
async def index(request: web.Request):
    return web.Response(text=INDEX_HTML, content_type='text/html')

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/ws', ws_handler)


if __name__ == "__main__":
    import ssl

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain("cert.pem", "key.pem")

    port = int(os.environ.get("PORT", "8443"))
    web.run_app(app, host="0.0.0.0", port=port, ssl_context=ssl_ctx)
 