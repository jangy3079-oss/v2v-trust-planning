"""Headless execution of the actual Pygame loop, key bindings and rendering."""
import os,sys
os.environ['SDL_VIDEODRIVER']='dummy'
os.environ['SDL_AUDIODRIVER']='dummy'
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.dont_write_bytecode=True
import pygame,torch,app
torch.set_num_threads(1)
original=app.Session
sessions=[]
def fixture(*args,**kwargs):
    s=original(*args,**{**kwargs,'bg':False,'gap':15.})
    sessions.append(s)
    return s
app.Session=fixture
class Clock:
    def tick(self,fps):return 17
pygame.time.Clock=Clock
frames=0
flip=pygame.display.flip
def frame():
    global frames
    frames+=1
    flip()
    if frames in (1,2,3,4):
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN,scancode=pygame.KSCAN_B if frames==4 else pygame.KSCAN_C))   # C is now ignored
    if frames==20:
        pygame.image.save(pygame.display.get_surface(),str(HERE/'private/avoidance/ui_smoke.png'))
        pygame.event.post(pygame.event.Event(pygame.QUIT))
pygame.display.flip=frame
app.run_ui(0)
assert sessions[0].ctl.backend=='combined' and sessions[0].ctl.solver=='nmpc'
assert frames>=20 and sessions[0].obs is not None
print('PASS: actual UI loop, C/B key events, interpolation, rotated vehicle drawing, screenshot')
