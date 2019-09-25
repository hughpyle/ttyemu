#!/usr/bin/env python3
"ASR-33 terminal emulator"
import sys
import time
import threading
import tkinter
import tkinter.font
import abc
import subprocess
import os
import shlex
try:
    import pty
    import termios
except ImportError:
    pass
try:
    import paramiko
except ImportError:
    pass
try:
    import pygame
except ImportError:
    pass

COLUMNS = 72
TEXT_COLOR = (0x33, 0x33, 0x33)

def upper(char):
    "Converts a character to uppercase in the dumbest way possible."
    char = ord(char)
    char = (char - 32) & 127
    if char > 64:
        char = 32 | (char & 31)
    return chr(char+32)

def background_color():
    "Return a background color."
    # Mainly for debug purposes, each new surface
    # gets a new background color in debug mode.
    #r = random.randrange(192, 256)
    #g = random.randrange(192, 256)
    #b = random.randrange(192, 256)
    #return (r, g, b)
    return (0xff, 0xee, 0xdd)


class AbstractLine:
    "Efficiently represent a line of text with overstrikes"
    def __init__(self):
        self.extents = []

    def place_char(self, column, char):
        "Insert a character into an available extent."
        if char == ' ':
            return
        for i, (begin, text) in enumerate(self.extents):
            end = begin+len(text)
            if end == column:
                text = text + char
                self.extents[i] = (begin, text)
            elif end + 1 == column:
                text = text + ' ' + char
                self.extents[i] = (begin, text)
            # extend left? replace spaces?
        self.extents.append((column, char))

    def string_test(self, chars, column=0):
        """
        Insert a sequence of character, interpreting backspace, tab, and
        carriage return. Return value is final column.
        """
        for char in chars:
            if char == '\t':
                column = (column + 8) & -8
            elif char == '\r':
                column = 0
            elif char == '\b':
                column -= 1
            else:
                self.place_char(column, char)
                column += 1
            if column > 71:
                column = 71
            if column < 0:
                column = 0
        return column

    @staticmethod
    def unit_test(chars):
        "Test function"
        print("Test of", repr(chars))
        line = AbstractLine()
        line.string_test(chars)
        for begin, text in line.extents:
            print("    ", begin, repr(text))

SLOP = 4
class TkinterFrontend:
    "Front-end using tkinter"
    # pylint: disable=too-many-instance-attributes
    def __init__(self, terminal=None):
        self.fg='#%02x%02x%02x' % TEXT_COLOR
        bg='#%02x%02x%02x' % background_color()
        self.terminal = terminal
        self.root = tkinter.Tk()
        font = tkinter.font.nametofont("TkFixedFont").actual()
        font['size'] = 16
        font = tkinter.font.Font(**font)
        self.font = font
        self.font_width = font.measure('X')
        self.font_height = self.font_width * 10 / 6
        self.canvas = tkinter.Canvas(
            self.root,
            bg=bg,
            height=24 * self.font_height + SLOP*2,
            width=COLUMNS * self.font_width + SLOP*2)
        bbox = (0, 0, self.font_width, self.font_height)
        self.cursor_id = self.canvas.create_rectangle(bbox)
        self.root.bind('<Key>', self.key)
        xscrollbar = tkinter.Scrollbar(self.root, orient='horizontal')
        xscrollbar.grid(row=1, column=0, sticky='ew')
        yscrollbar = tkinter.Scrollbar(self.root)
        yscrollbar.grid(row=0, column=1, sticky='ns')
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.max_line = 0
        self.canvas.config(
            xscrollcommand=xscrollbar.set,
            yscrollcommand=yscrollbar.set,
            offset='%d,%d'%(-SLOP,-SLOP),
            scrollregion=(-SLOP, -SLOP, COLUMNS*self.font_width+SLOP, self.font_height+SLOP),
        )
        xscrollbar.config(command=self.canvas.xview)
        yscrollbar.config(command=self.canvas.yview)

    def key(self, event):
        "Handle a keyboard event"
        #print(event)
        if event.keysym == 'F5':
            self.terminal.backend.fast_mode ^= True
        elif event.keysym == 'Prior':
            self.canvas.yview_scroll(-1, 'pages')
        elif event.keysym == 'Next':
            self.canvas.yview_scroll(1, 'pages')
        elif event.char:
            if len(event.char) > 1 or ord(event.char) > 0xF000:
                # weird mac tk stuff
                pass
            else:
                self.terminal.backend.write_char(event.char)

    def postchars(self, chars):
        "Relay the characters from the backend to the controller"
        self.terminal.output_chars(chars)

    # pylint: disable=invalid-name
    def draw_char(self, line, column, char):
        "Draw a character on the screen"
        x = column * self.font_width
        y = line * self.font_height
        #print("drawing char", repr(char), "at", (x, y))
        self.canvas.create_text(
            (x, y),
            text=char,
            fill=self.fg,
            anchor='nw',
            font=self.font)
        # Yes, this creates an object for every character.  Yes, it is
        # disgusting, and gets hideously slow after a few thousand lines of
        # output.  The Tkinter front end is mainly intended for testing.
        if self.max_line < line:
            self.max_line = line

    def lines_screen(self):
        "Dummy"
        return self.max_line + 1

    # pylint: disable=invalid-name
    # pylint: disable=unused-argument
    def refresh_screen(self, scroll_base, cursor_line, cursor_column):
        "Tkinter refresh method mostly just moves the cursor"
        x0 = cursor_column * self.font_width
        y0 = cursor_line * self.font_height
        x1 = x0 + self.font_width
        y1 = y0 + self.font_height
        self.canvas.coords(self.cursor_id, (x0, y0, x1, y1))
        if self.max_line < cursor_line:
            self.max_line = cursor_line
        scr_height = (self.max_line + 1) * self.font_height
        self.canvas.config(
            scrollregion=(-SLOP, -SLOP, COLUMNS*self.font_width+SLOP, scr_height+SLOP))
        cy = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        y0 -= SLOP
        y1 += SLOP
        # slop makes these calculations weird and possibly incorrect
        #print('cursor[%s:%s] canvas[%s:%s]' % (y0, y1, cy, cy+height))
        if y0 < cy:
            self.canvas.yview_moveto(y0/scr_height)
        elif y1 > cy + height:
            self.canvas.yview_moveto((y1 - height + SLOP*2)/scr_height)

    def reinit(self):
        "Clear everything"
        self.canvas.delete('all')
        bbox = (0, 0, self.font_width, self.font_height)
        self.cursor_id = self.canvas.create_rectangle(bbox)

    def mainloop(self, terminal):
        "main loop"
        self.terminal = terminal
        self.root.mainloop()


class PygameFrontend:
    "Front-end using pygame for rendering"
    # pylint: disable=too-many-instance-attributes
    def __init__(self, target_surface=None, lines_per_page=8):
        pygame.init()
        self.font = pygame.font.SysFont('monospace', 24)
        self.font_width, self.font_height = self.font.size('X')
        self.width_pixels = COLUMNS * self.font_width
        if target_surface is None:
            pygame.display.set_caption('Terminal')
            dim = self.width_pixels, 22*self.font_height
            target_surface = pygame.display.set_mode(dim)  #, pygame.RESIZABLE)
            target_surface.fill(background_color())
            pygame.display.update()
        self.page_surfaces = []
        self.target_surface = target_surface
        self.lines_per_page = lines_per_page
        self.char_event_num = pygame.USEREVENT+1
        self.terminal = None

    def reinit(self, lines_per_page=None):
        "Clears and resets all terminal state"
        self.page_surfaces.clear()
        if lines_per_page:
            self.lines_per_page = lines_per_page

    def lines_screen(self):
        "Returns the number of lines on the screen"
        return self.target_surface.get_height() // self.font_height

    #def alloc_line(self, line_number):
    #    "Bookkeeping to make sure the cursor line is valid after a linefeed"
    #    # turned out unnecessary here
    #    page_number, page_line = divmod(line_number, self.lines_per_page)
    #    page_surface = alloc_page(page_number)
    #    rect1 = (0, page_line * self.font_height, self.width_pixels, self.font_height)
    #    page_surface.fill(background_color(), rect1)

    def alloc_page(self, i):
        "Returns the i'th page surface"
        while len(self.page_surfaces) <= i:
            page_surface = pygame.Surface((self.width_pixels, self.lines_per_page*self.font_height))
            page_surface.fill(background_color())
            self.page_surfaces.append(page_surface)
        return self.page_surfaces[i]

    def blit_page_to_screen(self, page_number, scroll_base):
        "Refreshes a single page surface to the screen"
        line0 = page_number * self.lines_per_page
        line1 = (page_number + 1) * self.lines_per_page
        if line1 < scroll_base:
            return # page is off top of screen
        if line0 > scroll_base + self.lines_screen():
            return # page is off bottom of screen
        dest = (0, self.font_height*(line0 - scroll_base))
        area = pygame.Rect(0, 0, self.width_pixels, self.lines_per_page*self.font_height)
        page_surface = self.page_surfaces[page_number]
        #print("blit page", page_number, dest, area)
        self.target_surface.blit(page_surface, dest, area)

    def draw_cursor(self, phys_line, column):
        "Draws the cursor"
        curs = pygame.Rect(
            self.font_width*column,
            self.font_height*phys_line,
            self.font_width, self.font_height)
        pygame.draw.rect(self.target_surface, TEXT_COLOR, curs, 1)

    def refresh_screen(self, scroll_base, cursor_line, cursor_column):
        "Refreshes the screen"
        cursor_phys_line = cursor_line - scroll_base
        for i in range(len(self.page_surfaces)):
            self.blit_page_to_screen(i, scroll_base)
        self.draw_cursor(cursor_phys_line, cursor_column)
        pygame.display.update()
        sys.stdout.flush()

    def draw_char(self, line, column, char):
        "Draws a character on the page backing"
        text = self.font.render(char, True, TEXT_COLOR)
        page_number, page_line = divmod(line, self.lines_per_page)
        page_surface = self.alloc_page(page_number)
        page_surface.blit(text, (self.font_width*column, self.font_height*page_line))

    def postchars(self, chars):
        "Post message with characters to render."
        pygame.event.post(pygame.event.Event(self.char_event_num, chars=chars))

    def handle_key(self, event):
        "Handle a keyboard event"
        if event.unicode:
            self.terminal.backend.write_char(event.unicode)
            pygame.display.update()
        elif event.key == pygame.K_F5:
            self.terminal.backend.fast_mode = True
        elif event.key == pygame.K_PAGEUP:
            self.terminal.page_up()
        elif event.key == pygame.K_PAGEDOWN:
            self.terminal.page_down()
        else:
            pass
            #print(event)

    def mainloop(self, terminal):
        "Run game loop"
        self.terminal = terminal
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.KEYDOWN:
                    self.handle_key(event)
                if event.type == pygame.KEYUP:
                    if event.key == pygame.K_F5:
                        self.terminal.backend.fast_mode = False
                if event.type == pygame.VIDEORESIZE:
                    # Extremely finicky, but it seems to work
                    height = event.dict['size'][1]
                    height = height // self.font_height * self.font_height
                    pygame.display.set_mode((self.width_pixels, height), pygame.RESIZABLE)
                    self.target_surface.fill(background_color())
                    self.terminal.scroll_into_view()
                    self.terminal.refresh_screen()
                if event.type == self.char_event_num:
                    self.terminal.output_chars(event.chars)

# pylint: disable=unused-argument,no-self-use,missing-docstring
class DummyFrontend:
    "Front end that does nothing except a minimal connection to the terminal"
    def __init__(self, terminal=None):
        self.terminal = terminal

    def postchars(self, chars):
        self.terminal.output_chars(chars)

    def draw_char(self, line, column, char):
        sys.stdout.write(char)
        sys.stdout.flush()

    def lines_screen(self):
        return 24

    def refresh_screen(self, scroll_base, cursor_phys_line, cursor_column):
        pass

    def reinit(self):
        pass

    def mainloop(self, terminal):
        self.terminal = terminal
        while True:
            chars = sys.stdin.buffer.read1(1).decode('ascii', 'replace')
            if not chars:
                return
            terminal.backend.write_char(chars)


class Terminal:
    "Class for keeping track of the terminal state."

    def __init__(self, frontend=None, backend=None):
        if backend is None:
            backend = LoopbackBackend()
        if frontend is None:
            frontend = DummyFrontend(self)
        self.line = 0
        self.column = 0
        self.scroll_base = 0
        self.max_line = 0
        self.frontend = frontend
        self.backend = backend
        self.lines = {}

    def reinit(self):
        "Discard all state"
        self.frontend.reinit()
        self.line = 0
        self.column = 0
        self.scroll_base = 0
        self.max_line = 0
        self.lines.clear()

    def alloc_line(self, line):
        try:
            return self.lines[line]
        except KeyError:
            return self.lines.setdefault(line, AbstractLine())

    def output_char(self, char, refresh=True):
        "Simulates a teletype for a single character"
        #print("output_char", repr(char))
        if char == '\n':
            self.line += 1
        elif char == '\r':
            self.column = 0
        elif char == '\t':
            self.column = (self.column + 7) // 8 * 8
        elif char == '\b':
            self.column -= 1
        elif char == '\f':
            self.reinit()
        elif char >= ' ':
            char = upper(char)
            self.alloc_line(self.line).place_char(self.column, char)
            self.frontend.draw_char(self.line, self.column, char)
            self.column += 1
        self.constrain_cursor()
        self.scroll_into_view()
        if refresh:
            self.refresh_screen()

    def lines_screen(self):
        "Returns the number of lines on the screen (from front-end)"
        return self.frontend.lines_screen()

    def refresh_screen(self):
        "Refreshes the screen (to front-end)"
        self.frontend.refresh_screen(self.scroll_base, self.line, self.column)

    def output_chars(self, chars, refresh=True):
        "Calls output_char in a loop without refreshing"
        for char in chars:
            self.output_char(char, False)
        if refresh:
            self.refresh_screen()

    def constrain_cursor(self):
        "Ensure cursor is not out of bounds"
        if self.line < 0:
            self.line = 0
        if self.column < 0:
            self.column = 0
        if self.column >= COLUMNS:
            self.column = COLUMNS-1

    def scroll_into_view(self, line=None):
        "Scroll line into view"
        if line is None:
            line = self.line
        if line < self.scroll_base:
            self.scroll_base = line
        if line >= self.scroll_base + self.lines_screen():
            self.scroll_base = line - self.lines_screen() + 1

    def page_down(self):
        "Scrolls the page down"
        self.scroll_base += self.lines_screen() // 2
        self.constrain_scroll()
        self.refresh_screen()

    def page_up(self):
        "Scrolls the page up"
        self.scroll_base -= self.lines_screen() // 2
        self.constrain_scroll()
        self.refresh_screen()

    def constrain_scroll(self):
        "Ensures scroll is in bounds"
        if self.line > self.max_line:
            self.max_line = self.line
        if self.scroll_base > self.max_line - self.lines_screen() + 1:
            self.scroll_base = self.max_line - self.lines_screen() + 1
        if self.scroll_base < 0:
            self.scroll_base = 0

class LoopbackBackend:
    "Just sends characters from the keyboard back to the screen"
    def __init__(self, postchars=lambda chars: None):
        self.postchars = postchars

    def write_char(self, char):
        "Echo back keyboard character"
        self.postchars(char)

    def thread_target(self):
        pass

class ParamikoBackend:
    "Connects a remote host to the terminal"
    def __init__(self, host, username, keyfile, postchars=lambda chars: None):
        self.fast_mode = False
        self.channel = None
        self.postchars = postchars
        self.host = host
        self.username = username
        self.keyfile = keyfile

    def write_char(self, char):
        "Sends a keyboard character to the host"
        if self.channel is not None:
            self.channel.send(char.encode())
        else:
            self.postchars(char)

    def thread_target(self):
        "Method for thread setup"
        ssh = paramiko.Transport((self.host, 22))
        key = paramiko.RSAKey.from_private_key_file(self.keyfile)
        ssh.connect(username=self.username, pkey=key)
        self.channel = ssh.open_session()
        self.channel.get_pty(term='tty33')
        self.channel.invoke_shell()
        while True:
            if self.fast_mode:
                data = self.channel.recv(1024)
                if not data:
                    break
                self.postchars(data.decode('ascii', 'replace'))
            else:
                byte = self.channel.recv(1)
                if not byte:
                    break
                self.postchars(byte.decode('ascii', 'replace'))
                time.sleep(0.1)
        self.channel = None
        self.postchars("Disconnected. Local mode.\r\n")


class FiledescBackend(abc.ABC):
    "Base classes for backends using os.read/write"
    def __init__(self, lecho=False, crmod=False, postchars=lambda chars: None):
        self.fast_mode = False
        self.channel = None
        self.postchars = postchars
        self.write_fd = None
        self.read_fd = None
        self.crmod = crmod
        self.lecho = lecho

    def write_char(self, char):
        if self.write_fd is not None:
            if self.crmod:
                char = char.replace('\r', '\n')
            os.write(self.write_fd, char.encode())
            if self.lecho:
                if self.crmod:
                    char = char.replace('\n', '\r\n')
                self.postchars(char)
        else:
            self.postchars(char)

    @abc.abstractmethod
    def setup(self):
        ...

    def teardown(self):
        if self.read_fd is not None:
            os.close(self.read_fd)
        if self.write_fd is not None:
            os.close(self.write_fd)
        self.read_fd = self.write_fd = None

    def thread_target(self):
        self.setup()
        while True:
            if self.fast_mode:
                data = os.read(self.read_fd, 1024)
                if not data:
                    break
                if self.crmod:
                    data = data.replace(b'\n', b'\r\n')
                self.postchars(data.decode('ascii', 'replace'))
            else:
                byte = os.read(self.read_fd, 1)
                if not byte:
                    break
                if self.crmod:
                    byte = byte.replace(b'\n', b'\r\n')
                self.postchars(byte.decode('ascii', 'replace'))
                time.sleep(0.1)
        self.teardown()
        self.postchars("Disconnected. Local mode.\r\n")

class PipeBackend(FiledescBackend):
    """Backend for a subprocess running in a pipe pair.
    Not very useful, but cross-platform."""
    def __init__(self, cmd, shell=False, **kwargs):
        super().__init__(**kwargs)
        self.cmd = cmd
        self.shell = shell
        self.proc = None

    def setup(self):
        "Starts the process and hooks up the file descriptors"
        self.proc = subprocess.Popen(
            self.cmd, shell=self.shell,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        self.write_fd = self.proc.stdin.fileno()
        self.read_fd = self.proc.stdout.fileno()

    def teardown(self):
        "Closes the file descriptors"
        # Is there a good way to close this other than let gc take care of it?
        self.proc = None
        self.read_fd = self.write_fd = None

class PtyBackend(FiledescBackend):
    """Backend for a subprocess running in a pipe pair.
    Not very useful, but cross-platform."""
    def __init__(self, cmd, shell=False, **kwargs):
        super().__init__(**kwargs)
        self.cmd = cmd
        if type(cmd) is str:
            if shell:
                self.args = ['sh', '-c', cmd]
            else:
                self.args = shlex.split(cmd)
        else:
            self.args = cmd

    def setup(self):
        "Starts the process and hooks up the file descriptors"
        pid, master = pty.fork()
        if pid:
            self.write_fd = self.read_fd = master
        else:
            try:
                attr = termios.tcgetattr(0)
                attr[3] &= ~(termios.ECHOE|termios.ECHOKE)
                attr[3] |= termios.ECHOPRT|termios.ECHOK
                attr[4] = termios.B110
                attr[5] = termios.B110
                attr[6][termios.VERASE] = b'#'
                attr[6][termios.VKILL] = b'@'
                termios.tcsetattr(0, termios.TCSANOW, attr)
                os.environ['TERM'] = 'tty33'
                os.execvp(self.args[0], self.args)
            except Exception as ex:
                os.write(2, str(ex).encode('ascii', 'replace'))
                os.write(2, b'\r\n')
                os._exit(126)
            os._exit(126)

    def teardown(self):
        "Closes the file descriptor"
        os.close(self.read_fd)
        self.read_fd = self.write_fd = None

def main(frontend, backend):
    "Main function"
    my_term = Terminal(frontend, backend)
    backend.postchars = frontend.postchars
    backend_thread = threading.Thread(target=backend.thread_target)
    backend_thread.start()
    frontend.mainloop(my_term)

main(TkinterFrontend(), PtyBackend('sh'))
#main(PygameFrontend(), LoopbackBackend())
#main(TkinterFrontend(), ConptyBackend('ubuntu'))
#main(PygameFrontend(), PipeBackend('py -3 -i -c ""', crmod=True, lecho=True))
#main(DummyFrontend(), LoopbackBackend())
#main(DummyFrontend(), PtyBackend('sh'))
#AbstractLine.unit_test('bold\rbold')
#AbstractLine.unit_test('___________\runderlined')
#AbstractLine.unit_test('b\bbo\bol\bld\bd')
#AbstractLine.unit_test('_\bu_\bn_\bd_\be_\br_\bl_\bi_\bn_\be_\bd')
#AbstractLine.unit_test('Tabs\tone\ttwo\tthree\tfour')
#AbstractLine.unit_test('Spaces  one     two     three   four    ')
#AbstractLine.unit_test(
#        'Test\tb\bbo\bol\bod\bd\t'
#        '_\bu_\bn_\bd_\be_\br_\bl_\bi_\bn_\be_\bd\t'
#        'bold\b\b\b\bbold\t'
#        '__________\b\b\b\b\b\b\b\b\b\bunderlined\t'
#        'both\b\b\b\b____\b\b\b\bboth\t'
#        'And here is some junk to run off the right hand edge.')
#AbstractLine.unit_test("Hello, world.  This line has some spaces.")
