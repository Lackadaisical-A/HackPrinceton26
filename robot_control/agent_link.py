"""The newest `agent` message from the assist process (Gemini's chosen action). Receives only; never sends."""
import json
import logging
import threading
import time

import zmq

MAX_AGE_S = 10.


class AgentInbox:
    def __init__(self,endpoint='tcp://127.0.0.1:5563'):
        self.endpoint = endpoint
        self.lock = threading.Lock()
        self.message,self.seen = None,0.
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self.run,daemon=True,name='Smart replay agent link')

    def start(self): self.thread.start()

    def latest(self):
        """The last action received, or None once it is older than MAX_AGE_S."""
        with self.lock:
            return self.message if time.monotonic()-self.seen<=MAX_AGE_S else None

    def run(self):
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        try:
            socket.setsockopt(zmq.LINGER,0)
            socket.setsockopt(zmq.RCVHWM,4)
            socket.setsockopt(zmq.MAXMSGSIZE,8192)
            socket.setsockopt_string(zmq.SUBSCRIBE,'agent ')
            socket.connect(self.endpoint)
            while not self.closed.is_set():
                if not socket.poll(200): continue
                try:
                    message = json.loads(socket.recv_string().partition(' ')[2])
                except (ValueError,UnicodeDecodeError,zmq.ZMQError):
                    continue
                if isinstance(message,dict):
                    with self.lock: self.message,self.seen = message,time.monotonic()
        except Exception:
            logging.getLogger('wearable').exception('Smart replay agent link stopped')
        finally:
            socket.close()
            context.term()

    def close(self):
        self.closed.set()
        if self.thread.is_alive(): self.thread.join(timeout=1)
