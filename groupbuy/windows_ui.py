"""Optional Windows driver. Not automatically invoked in offline mode."""
import hashlib
import os
import time
from .common import GateError, path_from


def search_input_region(frame, label, rectangles):
    """Unique visible outline enclosing the observed search button on its right."""
    tokens = [t for t in frame['tokens'] if t['text'] == label]
    if not tokens:
        raise GateError('observed_control_missing')
    if len(tokens) != 1:
        raise GateError('ambiguous_observed_control')
    box = tokens[0]['box']
    bx, by = sum(p[0] for p in box) / 4, sum(p[1] for p in box) / 4
    bh = max(p[1] for p in box) - min(p[1] for p in box)
    regions = []
    for x, y, width, height in sorted(set(rectangles), key=lambda r: r[2] * r[3], reverse=True):
        if not (width >= 4 * height and bh < height <= 5 * bh
                and x + .65 * width < bx < x + width and y < by < y + height
                and all(x <= px <= x + width and y <= py <= y + height for px, py in box)):
            continue
        if any(all(abs(a - b) <= .12 * height for a, b in zip((x, y, width, height), prior)) for prior in regions):
            continue  # Inner and outer edges of the same visible border.
        regions.append((x, y, width, height))
    if len(regions) != 1:
        raise GateError('search_input_outline_not_unique')
    return regions[0]


class WindowsDriver:
    def __init__(self, cancel_event=None):
        if os.name != 'nt':
            raise GateError('windows_required')
        self.cancel_event = cancel_event
        self.focus_initial = True
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
        self._cancel()
        windows = self.desktop.windows(title_re=profile.get('window_title_regex', '大众点评'), visible_only=True)
        windows = [w for w in windows if self.psutil.Process(w.process_id()).name().lower()
                   in {x.lower() for x in profile.get('process_names', ['WeChat.exe', 'Weixin.exe'])}]
        if len(windows) != 1:
            raise GateError('ambiguous_or_missing_window')
        w = windows[0]
        if self.focus_initial:
            w.set_focus()
            self.focus_initial = False
        if self.gui.GetForegroundWindow() != w.handle:
            raise GateError('foreground_changed')
        image = w.capture_as_image()
        if self.gui.GetForegroundWindow() != w.handle:
            raise GateError('foreground_changed')
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
                'origin': [rect.left, rect.top], 'image': image}

    def _current(self, frame, region=None, label=None):
        self._cancel()
        w = frame['wrapper']
        rect = w.rectangle()
        if (self.gui.GetForegroundWindow() != w.handle or
                [rect.width(), rect.height()] != frame['window_size'] or
                [rect.left, rect.top] != frame['origin']):
            raise GateError('window_changed')
        # Observe-to-action race is fail-closed when the screen changes.
        fresh = w.capture_as_image()
        if hashlib.sha256(fresh.tobytes()).hexdigest() != frame['frame_hash']:
            if region is None or fresh.crop(region).tobytes() != frame['image'].crop(region).tobytes():
                raise GateError('screen_changed_before_action')
            # Unrelated list animations must not invalidate an unchanged header.
            # Re-check the whole fresh screen for risk and duplicate labels first.
            import numpy as np
            found, _ = self.ocr(np.asarray(fresh))
            texts = [t for _, t, score in found or [] if score >= .85]
            if any(marker in '\n'.join(texts) for marker in frame.get('risk_markers', ())):
                raise GateError('verification_or_login')
            if label is not None and texts.count(label) != 1:
                raise GateError('observed_control_missing' if label not in texts else 'ambiguous_observed_control')
            if (self.gui.GetForegroundWindow() != w.handle
                    or w.capture_as_image().crop(region).tobytes() != fresh.crop(region).tobytes()):
                raise GateError('screen_changed_before_action')
        final_rect = w.rectangle()
        if (self.gui.GetForegroundWindow() != w.handle or
                [final_rect.width(), final_rect.height()] != frame['window_size'] or
                [final_rect.left, final_rect.top] != frame['origin']):
            raise GateError('window_changed')
        return w

    def click_observed(self, label, frame):
        candidates = [t for t in frame['tokens'] if t['text'] == label]
        if not candidates:
            raise GateError('observed_control_missing')
        if len(candidates) != 1:
            raise GateError('ambiguous_observed_control')
        box = candidates[0]['box']
        x = round(sum(p[0] for p in box) / len(box))
        y = round(sum(p[1] for p in box) / len(box))
        pad = max(p[1] for p in box) - min(p[1] for p in box)
        region = (max(0, int(min(p[0] for p in box) - pad)), max(0, int(min(p[1] for p in box) - pad)),
                  min(frame['window_size'][0], int(max(p[0] for p in box) + pad)),
                  min(frame['window_size'][1], int(max(p[1] for p in box) + pad)))
        self._click_point(frame, x, y, region, label)

    def focus_search_observed(self, label, frame):
        import cv2
        import numpy as np
        gray = cv2.cvtColor(np.asarray(frame['image']), cv2.COLOR_RGB2GRAY)
        contours, _ = cv2.findContours(cv2.Canny(gray, 60, 160), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        x, y, width, height = search_input_region(frame, label, [cv2.boundingRect(c) for c in contours])
        self._click_point(frame, round(x + .35 * width), round(y + height / 2), (x, y, x + width, y + height), label)

    def _click_point(self, frame, x, y, region=None, label=None):
        w = self._current(frame, region, label)
        self._hit(w, frame, x, y)
        from pywinauto import mouse
        mouse.click(coords=(frame['origin'][0] + x, frame['origin'][1] + y))
        self.input_window = w.handle

    def _hit(self, w, frame, x, y):
        hit = self.gui.WindowFromPoint((frame['origin'][0] + x, frame['origin'][1] + y))
        if hit != w.handle and not self.gui.IsChild(w.handle, hit):
            raise GateError('window_occluded')

    def type_search(self, text):
        self._cancel()
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
        if self.cancel_event is not None:
            if self.cancel_event.wait(seconds):
                raise KeyboardInterrupt
        else:
            time.sleep(seconds)

    def _cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise KeyboardInterrupt
