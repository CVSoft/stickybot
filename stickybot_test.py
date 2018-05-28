import time

import stickybot

class StickybotTest(object):
    def __init__(self):
        with open("stickybot_test.txt", "r") as cfgfile:
            cfg = list(filter(None, cfgfile.read().split('\n')))
            self.server = cfg[0] # first line
            self.port = cfg[1] # second line ...
            self.nick = cfg[2]
            self.channel = cfg[3]
            self.stickybot = stickybot.Stickybot((self.server, int(self.port)),
                                                 self.nick)
            
    def connect(self):
        print("Connecting to {s.server}:{s.port} as {s.nick}".format(s=self))
        self.stickybot.connect()
        while not self.stickybot.ready: time.sleep(0.001)
        print("Connected!")
        self.stickybot.join(self.channel)
        time.sleep(0.5) # give it time for the server to catch up
        self.stickybot.privmsg(self.channel, "Hello world!")

    def do_stuff(self):
        line = self.stickybot.recv() # grab a line off the buffer
        if not line: return # since we're async, there's not always a line
        if line.printable: print(str(line)) # if it's not a control command
        # (like a PRIVMSG you can see), then display a readable form of it

if __name__ == "__main__":
    s = StickybotTest()
    try:
        s.connect()
        while True:
            s.do_stuff()
            time.sleep(0.02) # lowers CPU load
    except KeyboardInterrupt:
        s.stickybot.disconnect()
        print("Exited!")
