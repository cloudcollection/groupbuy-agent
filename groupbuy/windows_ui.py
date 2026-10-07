"""Optional Windows driver. Not automatically invoked in offline mode."""
import hashlib
import os
import time
from .common import GateError, path_from


class WindowsDriver:
    def __init__(self):
        if os.name != 'nt':
            raise GateError('windows_required')
        # Dependencies and models must live on D:. No global user cache is used.
        temp = os.environ.get('GB_TEMP_DIR')
        if not temp:
            raise GateError('temp_directory_required')
        temp = path_from('.', temp, write=True)
        temp.mkdir(parents=True, exist_ok=True)
        os.environ['TEMP'] = os.environ['TMP'] = str(temp)
        try:
            from rapidocr_onnxruntime import RapidOCR
            from pywinauto import Desktop
            import win32gui
            import psutil
        except ImportError:
            raise GateError('optional_ui_dependencies_missing') from None
        self.ocr, self.desktop, self.gui, self.psutil = RapidOCR(), Desktop(backend='win32'), win32gui, psutil

    def observe(self, profile):
        windows = self.desktop.windows(title_re=profile.get('window_title_regex', '大众点评'), visible_only=True)
        windows = [w for w in windows if self.psutil.Process(w.process_id()).name().lower()
                   in {x.lower() for x in profile.get('process_names', ['WeChat.exe', 'Weixin.exe'])}]
        if len(windows) != 1:
            raise GateError('ambiguous_or_missing_window')
        w = windows[0]
        image = w.capture_as_image()
        import numpy as np
        found, _ = self.ocr(np.asarray(image))
        tokens = [{'box': b, 'text': t, 'confidence': float(s)} for b, t, s in found or [] if s >= .85]
        anchors = [t for t in tokens if t['text'] == profile.get('list_anchor')]
        if len(anchors) > 1:
            raise GateError('list_anchor_not_unique')
        top = int(max(y for x, y in anchors[0]['box'])) + 2 if anchors else 0
        if anchors and not 0 < top < image.height - 80:
            raise GateError('invalid_observed_list_region')
        crop = image.crop((0, top, image.width, image.height))
        list_tokens = [t for t in tokens if min(y for x, y in t['box']) > top]
        rect = w.rectangle()
        return {'window_id': w.handle, 'window_size': [image.width, image.height],
                'foreground': self.gui.GetForegroundWindow() == w.handle,
                'text': '\n'.join(t['text'] for t in tokens), 'tokens': tokens,
                'list_text': '\n'.join(t['text'] for t in list_tokens) if anchors else '',
                'list_observed': bool(anchors),
                'frame_hash': hashlib.sha256(image.tobytes()).hexdigest(),
                'list_hash': hashlib.sha256(crop.tobytes()).hexdigest(),
                'list_region': [0, top, image.width, image.height], 'wrapper': w,
                'origin': [rect.left, rect.top]}

    def _current(self, frame):
        w = frame['wrapper']
        rect = w.rectangle()
        if (self.gui.GetForegroundWindow() != w.handle or
                [rect.width(), rect.height()] != frame['window_size'] or
                [rect.left, rect.top] != frame['origin']):
            raise GateError('window_changed')
        # Observe-to-action race is fail-closed when the screen changes.
        if hashlib.sha256(w.capture_as_image().tobytes()).hexdigest() != frame['frame_hash']:
            raise GateError('screen_changed_before_action')
        return w

    def click_observed(self, label, frame):
        candidates = [t for t in frame['tokens'] if t['text'] == label]
        if len(candidates) != 1:
            raise GateError('ambiguous_observed_control')
        w = self._current(frame)
        box = candidates[0]['box']
        x = round(sum(p[0] for p in box) / len(box))
        y = round(sum(p[1] for p in box) / len(box))
        self._hit(w, frame, x, y)
        from pywinauto import mouse
        mouse.click(coords=(frame['origin'][0] + x, frame['origin'][1] + y))
        self.input_window = w.handle

    def _hit(self, w, frame, x, y):
        hit = self.gui.WindowFromPoint((frame['origin'][0] + x, frame['origin'][1] + y))
        if hit != w.handle and not self.gui.IsChild(w.handle, hit):
            raise GateError('window_occluded')

    def type_search(self, text):
        # Literal Unicode input; braces are escaped for pywinauto key syntax.
        from pywinauto.keyboard import send_keys
        if not getattr(self, 'input_window', None) or self.gui.GetForegroundWindow() != self.input_window:
            raise GateError('foreground_changed')
        escaped = ''.join('{%s}' % c if c in '{}+^%~()' else c for c in text)
        send_keys('^a' + escaped + '{ENTER}', with_spaces=True, pause=.03, vk_packet=True)

    def scroll_observed(self, frame, steps):
        if not frame.get('list_observed'):
            raise GateError('list_anchor_not_unique')
        w = self._current(frame)
        left, top, right, bottom = frame['list_region']
        x, y = round((left + right) / 2), round((top + bottom) / 2)
        self._hit(w, frame, x, y)
        from pywinauto import mouse
        mouse.scroll(coords=(frame['origin'][0] + x, frame['origin'][1] + y), wheel_dist=-steps)

    def wait(self, seconds):
        time.sleep(seconds)
