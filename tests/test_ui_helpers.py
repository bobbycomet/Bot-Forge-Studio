import sys, asyncio; sys.path.insert(0,'../botstudio/runtime')
import discord
import bs_runtime as bs

class Resp:
    def __init__(s): s.done=False; s.sent=[]; s.modal=None; s.edited=[]
    def is_done(s): return s.done
    async def send_message(s,**k):
        s.done=True; s.sent.append(k)
        v=k.get('view')
        if v is not None and v.timeout: asyncio.get_event_loop().call_later(v.timeout, v.stop)   # real discord.py starts this when it stores the view
    async def send_modal(s,m): s.done=True; s.modal=m
    async def edit_message(s,**k): s.edited.append(k)
class Msg:
    async def edit(s,**k): pass
class Follow:
    def __init__(s): s.sent=[]
    async def send(s,**k): s.sent.append(k); return Msg()
class FakeUser:
    id=7
class I:
    def __init__(s): s.response=Resp(); s.followup=Follow(); s.user=FakeUser()
    async def original_response(s): return Msg()

async def main():
    # buttons: click "no"
    i=I()
    async def click():
        await asyncio.sleep(.05)
        view=i.response.sent[0]["view"]
        btn=[c for c in view.children if c.label=="No"][0]
        j=I(); await btn.callback(j)
    asyncio.get_event_loop().create_task(click())
    cid,label,j=await bs.ask_buttons(i,"Sure?",None,[("Yes","yes","success",""),("No","no","danger","")],timeout=2)
    print("buttons ->",cid,label,type(j).__name__, j.response.edited)
    # timeout
    i=I(); r=await bs.ask_buttons(i,"x",None,[("A","a","primary","")],timeout=.1); print("timeout ->",r)
    # menu
    i=I()
    async def pick():
        await asyncio.sleep(.05)
        sel=i.response.sent[0]["view"].children[0]; sel._values=["red"]
        sel._selected_values=["red"]
        await sel.callback(I())
    asyncio.get_event_loop().create_task(pick())
    vals,j=await bs.ask_menu(i,"pick",None,[("Red","red","",""),("Blue","blue","","")],timeout=2); print("menu ->",vals)
    # form
    i=I()
    async def submit():
        await asyncio.sleep(.05)
        m=i.response.modal; tis=[c for c in m.children]
        for t,v in zip(tis,["Bobby","hello"]): t._value=v
        await m.on_submit(I())
    asyncio.get_event_loop().create_task(submit())
    ans,j=await bs.ask_form(i,"T",[["Name","name","short","yes","","",""],["About","about","long","no","","",""]]); print("form ->",ans)
    # paginate
    i=I(); await bs.paginate(i,bs.split_pages("a\n---\nb\n---\nc"),"T"); v=i.response.sent[0]["view"]; k=I()
    await v.next.callback(k) if False else await v._go(k,1); print("page ->",k.response.edited[0]["embed"].description)
    # tabs
    i=I(); await bs.tabbed_menu(i,[["Rules","Be kind","","",""],["Help","Ask","","",""]]); v=i.response.sent[0]["view"]; v.sel._values=["1"]; k=I(); await v._pick(k); print("tabs ->",k.response.edited[0]["embed"].title)
asyncio.run(main())
