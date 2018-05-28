import re
import socket
import sys
import threading
import time

from collections import deque
import queue

VERSION = "v1.07"
WAIT = 0.5 # maximum time to wait for a command to clear

def strip_color(s):
    return re.sub("(?:\x03[0-9]{1,2}(?:,[0-9]{1,2})|\x02|\x0b|\x0f|\x1d|\x1f)",
                  "", s)

def peek_command(s):
    if s.startswith(":"): s = s.partition(" ")[2]
    return s.partition(" ")[0]


class SocketHandler(object):
    def __init__(self, cb):
        """Does the dirty work for us."""
        self.cb = cb # callback to Stickybot
        self.send_queue = queue.Queue()
        self.recv_queue = deque()
        self.recv_buffer = b""
        self.running = False
        self.err_closed = False

    def start(self):
        """Reset everything and start the threads"""
##        self.send_queue = queue.Queue()
##        self.recv_queue.clear()
##        self.recv_buffer = b""
        self.err_closed = False
        self.sock = socket.socket()
        self.sock.setblocking(0)
        self.sock.settimeout(4)
        self.conn = None
        self.running = True
        self.thread_rx = threading.Thread(target=self.net_rx, daemon=True)
        self.thread_rx.start()
        self.thread_tx = threading.Thread(target=self.net_tx, daemon=True)
        self.thread_tx.start()

    def kill(self): # haha me too thanks
        """Stop the socket and try to kill the threads"""
        if self.cb.verbose: print("KILLING MYSELF")
        self.running = False
        try: self.sock.close()
        except socket.error: pass

    def net_rx(self):
        """Threaded loop for receiving data from server"""
        self.conn = None
        try:
            self.sock.connect(self.cb.addr)
            self.conn = True
        except socket.error:
            if self.cb.verbose:
                print("Failed to connect to server {}:{}".format(*self.cb.addr))
                self.err_closed = True
        if not self.conn:
            self.running = False
        while self.running:
            try:
                self.recv_buffer += self.sock.recv(2048)
            except socket.timeout: pass # non-blocking socket
            except socket.error:
                self.err_closed = True
                self.kill()
                if self.cb.verbose: print("a socket died pretty horribly")
            except:
                self.err_closed = True
                self.kill()
                if self.cb.verbose:
                    print(sys.exc_info())
                    print("a socket probably tried too late to live")
            while True:
                l, _, self.recv_buffer = self.recv_buffer.partition(b'\n')
                if _ != b'\n': break
                with open("debug_rawirc.txt", "ab") as f:
                    f.write(l+b'\n')
                    if self.cb.verbose: sys.stderr.write(l.decode("utf-8")+'\n')
                l = l.rstrip(b'\r')
                # deal with pingpong
                cmd = peek_command(l.decode("utf-8"))
                if cmd == "PING": self.send(Line(l).pong())
                elif cmd == "001" and not self.cb.ready:
                    self.cb.ready = True
                else: self.recv_queue.append(l) # make CRLF into LF
            time.sleep(0.001) # 1ms wait to keep CPU down?

    def net_tx(self):
        """Threaded loop for sending data to server"""
        if self.conn == False:
            if self.cb.verbose: print("nothing to do here")
            return 
        while not self.conn: time.sleep(0.001) # wait for net_rx to set up
        send_ts = time.time() - self.cb.cooldown
        while self.running:
##            print("I have {} items to send!"\
##                  .format(self.send_queue.qsize()))
            to_send = self.send_queue.get()
##            print(" I may have {} items to send!"\
##                  .format(self.send_queue.qsize()+1))
##            print(to_send.l, file=sys.stderr)
            # do throttling checks on certain messages, like PRIVMSG
            if not to_send.throttle or \
               time.time() - send_ts > self.cb.cooldown:
                try:
                    self.sock.send(bytes(to_send.l+"\r\n", "utf-8"))
                    if self.cb.verbose: sys.stderr.write(to_send.l+'\n')
                except socket.error:
##                        self.send_queue.task_done()
                    self.err_closed = True
                    self.kill()
                    print("net_tx: fatal error")
                self.send_queue.task_done()
            time.sleep(0.001)

    def read(self):
        """Pops a line from the receive queue"""
        if not self.recv_queue: return None
        return Line(self.recv_queue.popleft())

    def peek(self):
        """Look at the most recent raw data received"""
        if not self.recv_queue: return None
        return self.recv_queue[0].decode("utf-8")

    def send(self, l):
        """Adds a line to the sending queue"""
        if not isinstance(l, OutgoingLine): raise TypeError
        self.send_queue.put(l)


class User(object):
    def __init__(self, hostmask):
        self.hostmask = hostmask
        if hostmask == "__server":
            self.nick = "__server"
            self.user = "server"
            self.host = "debug.stickybot"
        else:
            self.nick, _, hostmask = hostmask.partition('!')
            self.user, _, self.host = hostmask.partition('@')

    def __repr__(self):
        return self.hostmask

    def __str__(self):
        return self.nick


class OutgoingLine(object):
    def __init__(self, cmd, **kwargs):
        self.throttle = False
        self.cmd = cmd.upper()
        getattr(self, "do_{}".format(self.cmd), self.do_QUOTE)(cmd, kwargs)
##        print("Creating a send for \"{}\"".format(self.l))
        
    def do_QUOTE(self, cmd, kwargs):
        self.l = kwargs["msg"]
        
    def do_PRIVMSG(self, cmd, kwargs):
        self.throttle = True
        if "ctcp" in kwargs:
            if kwargs["ctcp"]:
                kwargs["msg"] = "\x01{}\x01".format(kwargs["msg"])
                del kwargs["ctcp"]
        self.l = "PRIVMSG {channel} :{msg}".format(**kwargs)
            
    def do_NOTICE(self, cmd, kwargs): # use NOTICE for outgoing CTCPs
        self.throttle = True
        if "ctcp" in kwargs:
            if kwargs["ctcp"]:
                kwargs["msg"] = "\x01{}\x01".format(kwargs["msg"])
                del kwargs["ctcp"]
        self.l = "NOTICE {channel} :{msg}".format(**kwargs)
            
    def do_PONG(self, cmd, kwargs):
        self.l = "PONG {msg}".format(**kwargs)

    def do_NICK(self, cmd, kwargs):
        self.l = "NICK {msg}".format(**kwargs)

    def do_USER(self, cmd, kwargs):
        if "flags" not in kwargs: kwargs["flags"] = "8"
        self.l = "USER {user} {flags} * :{realname}".format(**kwargs)

    def do_JOIN(self, cmd, kwargs):
        self.throttle = True
        self.l = "JOIN {channel}".format(**kwargs)
        if "key" in kwargs:
            if kwargs["key"]:
                self.l += " {key}".format(**kwargs)

    def do_PART(self, cmd, kwargs):
        self.throttle = True
        self.l = "PART {channel}".format(**kwargs)
        if "msg" in kwargs:
            if kwargs["msg"]:
                self.l += " :{msg}".format(**kwargs)

    def do_QUIT(self, cmd, kwargs):
        self.l = "QUIT :{msg}".format(**kwargs)


class Line(object):
    def __init__(self, l):
        """Parses and stores lines at the same time!"""
        l = l.decode("utf-8")
        self.l = l
        self.src = User("__server")
        self.cmd = None
        self.msg = None
        self.ctcp = None
        self.printable = True
        # figure out where this came from
        if l.startswith(':'):
            l = l[1:]
            self.src, _, l = l.partition(' ')
            self.src = User(self.src)
        self.cmd, _, l = l.partition(' ')
        self.cmd = self.cmd.upper()
        # figure out whaat this actually is
        # at this point, source and command have already been parsed and clipped
        getattr(self, "cmd_{}".format(self.cmd), self.cmd_unknown)(l)
        if self.msg: self.msg = strip_color(self.msg)
        
    def __str__(self):
        if self.cmd == "PRIVMSG":
            if self.ctcp != "ACTION":
                return "{s.channel}: <{s.src}> {s.msg}".format(s=self)
            else: return "{s.channel}:  * {s.src} {s.msg}".format(s=self)
        elif self.cmd == "MODE":
            return "*** {s.src} sets mode {s.channel} {s.msg}".format(s=self)
        elif self.cmd == "TOPIC":
            return "*** {s.src} sets {s.channel} topic to {s.msg}".format(s=self)
        elif self.cmd == "JOIN":
            return "*** Joins {s.channel}: {s.src} ({u.hostmask})"\
                   .format(s=self, u=self.src)
        elif self.cmd == "332":
            return "*** Topic of {s.channel} is {s.msg}".format(s=self)
        elif self.cmd == "333":
            return "*** Topic set by {s.msg} on {s.ts}".format(s=self)
        elif self.cmd == "353":
            return "*** Users in {s.channel}: {s.msg}".format(s=self)
        else:
            if self.printable:
                if self.msg: return self.msg
                else: return self.l
            else: return ""

    def __repr__(self):
        return "Line(\"{s.l}\")".format(s=self)

    def pong(self):
        if self.cmd == "PING":
            return OutgoingLine("PONG", msg=self.l[5:])
        
    def cmd_PING(self, l):
        self.msg = self.l

    def cmd_PRIVMSG(self, l):
        self.channel, _, self.msg = l.partition(' :')
        if self.msg.endswith("\x01") and self.msg.startswith("\x01") and \
           len(self.msg) > 3:
            self.msg = self.msg[1:-1]
            self.ctcp, _, self.msg = self.msg.partition(" ")
        else: self.ctcp = None

    def cmd_NOTICE(self, l):
        self.channel, _, self.msg = l.partition(' :')
        if self.msg.endswith("\x01") and self.msg.startswith("\x01") and \
           len(self.msg) > 3:
            self.msg = self.msg[1:-1]
            self.ctcp, _, self.msg = self.msg.partition(" ")
        else: self.ctcp = None

    def cmd_MODE(self, l):
        self.channel, _, self.msg = l.partition(" ")

    def cmd_JOIN(self, l):
        _, _, self.channel = l.partition(" :")

    def cmd_PART(self, l):
        self.channel, _, self.msg = l.partition(" :")

    def cmd_TOPIC(self, l):
        self.channel, _, self.msg = l.partition(" :")

    # brace yourself

    def cmd_001(self, l):
        self.channel, _, self.msg = l.partition(" :")

    def cmd_002(self, l):
        self.channel, _, self.msg = l.partition(" :")
        self.printable = False

    def cmd_003(self, l):
        self.channel, _, self.msg = l.partition(" :")
        
    def cmd_004(self, l):
        self.channel, _, l = l.partition(" ")
        l = l.split(" ")
        self.msg = "You are on {} running {}".format(*l[:2])

    def cmd_005(self, l):
        self.printable = False

    def cmd_251(self, l):
        self.channel, _, self.msg = l.partition(" :")

    def cmd_252(self, l):
        self.channel, _, l = l.partition(" ")
        self.msg = l.replace(":", "")

    def cmd_254(self, l):
        self.channel, _, l = l.partition(" ")
        self.msg = l.replace(":", "")

    def cmd_255(self, l):
        self.channel, _, self.msg = l.partition(" :")

    def cmd_265(self, l):
        self.channel, _, l = l.partition(" ")
        lu, _, l = l.partition(" ")
        mu, _, self.msg = l.partition(" :")
        self.local_users = int(lu)
        self.local_users_max = int(mu)

    def cmd_266(self, l):
        self.channel, _, l = l.partition(" ")
        gu, _, l = l.partition(" ")
        mu, _, self.msg = l.partition(" :")
        self.global_users = int(gu)
        self.global_users_max = int(mu)

    def cmd_332(self, l):
        l = l.partition(" ")[2]
        self.channel, _, self.msg = l.partition(" :")

    def cmd_333(self, l):
        l = l.partition(" ")[2]
        self.channel, _, l = l.partition(" ")
        l = l.split(' ')
        self.msg = l[0]
        self.ts = time.asctime(time.localtime(int(l[1])))

    def cmd_353(self, l):
        l = l.partition(" = ")[2]
        self.channel, _, l = l.partition(" :")
        self.msg = ", ".join(filter(None, sorted(l.strip().split(' '))))

    def cmd_366(self, l):
        l = l.partition(" ")[2]
        self.channel, _, self.msg = l.partition(" :")

    def cmd_422(self, l):
        self.channel, _, self.msg = l.partition(" :")

    def cmd_unknown(self, l):
        pass
##        print("Unknown command for the following line:")
##        print(self.l)


class Stickybot(object):
    def __init__(self, addr, nick):
        """Initialize a new Stickybot."""
        self.addr = addr # (hostname, port)
        self.password = None # server password for PASS auth
        # user profile
        self.nick = nick
        self.user = nick
        self.realname = nick
        # where to actually go
        self.channels = []
        self.ready = False
        # quirks
        self.ctcp = {"VERSION":"Stickybot IRC Framework {}".format(VERSION)}
        self.verbose = False
        self.cooldown = 0.55 # interval to send lines at, basically
        # essentials
        self.sh = SocketHandler(self)

    def send(self, l):
        """Send a raw IRC line or an OutgoingLine to the IRC server"""
        if not isinstance(l, OutgoingLine):
            l = OutgoingLine("QUOTE", msg=l)
##        self.sh.send_queue.put(l)
        self.sh.send(l)

    def recv(self):
        """Read a (formatted? idk it's all the same) line from the IRC server"""
        return self.sh.read()

    def status(self):
        """returns (sockethandler is running, sockethandler crashed)"""
        return self.sh.running, self.sh.err_closed

    def connect(self):
        self.sh.start()
        time.sleep(0.5) # shouldn't have to do this! ~ race condition?
        self.set_nick(self.nick)
        time.sleep(1.75) # some servers get pissy
        self.send_user()

    def disconnect(self):
        self.quit("Console closed")
        self.sh.kill()

    def set_nick(self, nick):
        self.send(OutgoingLine("NICK", msg=nick))
        t = time.time()
        success = True
        while time.time() - t < WAIT:
            l = self.sh.peek()
            if not l: continue
            # if see self change nick, success = True; break
            if l.split(' ')[1] == "338": success = False
        if success: self.nick = nick

    def send_user(self):
        self.send(OutgoingLine("USER", user=self.user,
                               realname = self.realname))

    def join(self, ch, key=None):
        self.send(OutgoingLine("JOIN", channel=ch, key=key))

    def part(self, ch, msg=None):
        self.send(OutgoingLine("PART", channel=ch, msg=msg))

    def quit(self, msg=None):
        self.send(OutgoingLine("QUIT", msg=msg))

    def privmsg(self, ch, msg, ctcp=False):
        self.send(OutgoingLine("PRIVMSG", channel=ch, msg=msg, ctcp=ctcp))

    def notice(self, ch, msg, ctcp=False):
        self.send(OutgoingLine("NOTICE", channel=ch, msg=msg, ctcp=ctcp))

    def handle_ctcp(self, l):
        """Handle CTCP dynamically, or use a static response"""
        if l.ctcp == "ACTION": pass # no need to respond
        elif l.ctcp == "TIME":
            self.send(OutgoingLine("NOTICE", msg=time.asctime(), ctcp=True))
        elif l.ctcp.upper() in self.ctcp:
            self.send(OutgoingLine("NOTICE", msg=self.ctcp[l.ctcp.upper()],
                                   ctcp=True))
        elif l.ctcp and self.verbose:
            print("Unhandled CTCP for {}".format(repr(l)))

        
if __name__ == "__main__":
    input("ENTER to continue")
    print("Starting a debug instance of StickyBot")
    s = Stickybot(("104.32.6.222", 6667), "stickybot")
    s.verbose = True
    s.connect()
    try:
        joined = False
        while True:
            if not joined:
                if s.ready:
                    s.join("#bosaiknet")
                    joined = True
            msg = s.recv()
            if msg:
                if msg.printable: print(str(msg)) # print(msg.cmd+" "+str(msg))
            time.sleep(0.01)
    except KeyboardInterrupt:
        s.disconnect()
