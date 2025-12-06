import sys, os, gc
import numpy as np
import imageio.v2 as imageio
from PIL import Image

from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QColor, QPainter, QFont, QFontMetrics
from PyQt5.QtWidgets import QLabel, QMainWindow, QApplication, QSizePolicy

import pvsubfunc

DEF_SOUND_OK = "ok.wav"
DEF_SOUND_NG = "ng.wav"
APP_WIDTH = 320
APP_HEIGHT = 320

WINDOW_TITLE = "MovieToFrameImage 0.1.2"
SETTINGS_FILE = "MovieToFrameImage.json"
GEOMETRY_X = "geometry-x"
GEOMETRY_Y = "geometry-y"
GEOMETRY_W = "geometry-w"
GEOMETRY_H = "geometry-h"
SOUND_FILE_OK = "sound-file-ok"
SOUND_FILE_NG = "sound-file-ng"

# -------------------------------
# フレーム読み込みスレッド
# -------------------------------
class FrameLoader(QThread):
    progress = pyqtSignal(list, int, int, float)    # frames, 現在フレーム数, 総フレーム数, fps
    finished = pyqtSignal(list)                     # frames
    error = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self._is_running = True
    def run(self):
        total_frames = 0
        try:
            ext = os.path.splitext(self.path)[1].lower()
            frames = []
            idx = 0
            framerate = 0.0

            if ext == ".webp":
                img = Image.open(self.path)
                total_frames = img.n_frames
                # 本来webpはフレームごとのウェイト（可変フレームレート）に対応しているが、
                # このソフトでは固定フレームレート（先頭フレームの表示時間から算出）として処理する（手抜き）
                dur_ms = img.info.get("duration", 66)
                framerate = 1.0 / (dur_ms / 1000.0)
                while self._is_running:
                    frame = np.array(img.convert("RGB"))
                    frames.append(frame)
                    self.progress.emit(frames, idx, total_frames, framerate)
                    idx += 1
                    try:
                        img.seek(img.tell() + 1)
                    except EOFError:
                        break
                img = None
                self.finished.emit(frames)

            elif ext == ".mp4":
                reader = imageio.get_reader(self.path)
                total_frames = reader.count_frames()
                # mp4も固定のフレームレートとして処理する（手抜き）
                framerate = reader.get_meta_data().get("fps", 15.0)

                for idx, fr in enumerate(reader):
                    frames.append(fr)
                    self.progress.emit(frames, idx, total_frames, framerate)

                reader.close()
                self.finished.emit(frames)

            else:
                self.error.emit("Unsupported format")

        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._is_running = False

# -------------------------------
# メインウインドウ
# -------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(WINDOW_TITLE)
        self.setGeometry(100, 100, APP_WIDTH, APP_HEIGHT)
        self.setAcceptDrops(True)

        # 状態管理
        self.playlist = []
        self.current_index = -1
        self.loader = None
        self.frames = []
        self.durations = []
        self.fps = 0.0
        self.current_frame = 0
        self.total_frame = 0
        self.loaded_frame = 0
        self.playing = False
        self.dummyimage = None

        self.pydir = os.path.dirname(os.path.abspath(__file__))
        self.soundOK = DEF_SOUND_OK
        self.soundNG = DEF_SOUND_NG
        #設定ファイルがあれば読み込み
        if os.path.exists(SETTINGS_FILE):
            self.load_settings()

        # タイマー
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.next_frame)

        # UI
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setMinimumSize(APP_WIDTH, APP_HEIGHT)

        self.info_label = QLabel("動画ファイルかディレクトリをドロップしてください")
        self.info_label.setAlignment(Qt.AlignLeft)
        self.info_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.label, stretch=1)
        layout.addWidget(self.info_label)

        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        self.setCentralWidget(widget)

    # --------------------------------
    # Drag & Drop
    # --------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        paths = [u.toLocalFile() for u in urls]

        collected = []

        for path in paths:
            if os.path.isdir(path):
                # ディレクトリ → 直下の mp4/webp を対象にする
                for f in os.listdir(path):
                    full = os.path.join(path, f)
                    if os.path.isfile(full) and f.lower().endswith((".mp4", ".webp")):
                        collected.append(full)
            else:
                # 単一ファイル
                if path.lower().endswith((".mp4", ".webp")):
                    collected.append(path)

        # 対象がない
        if not collected:
            return

        # playlist を入れ替え
        self.playlist = collected
        self.current_index = 0

        self.load_current()  # ←必ずロード
        self.raise_()
        self.activateWindow()

    # --------------------------------
    # 動画読み込み開始
    # --------------------------------
    # ロード
    def load_current(self):
        path = self.playlist[self.current_index]
        self.setWindowTitle(path)
        self.current_frame = 0
        self.total_frame = 0
        self.loaded_frame = 0

        if self.loader != None:
            self.loader.stop()
            self.loader.wait()
        self.loader = None
        self.frames = None
        gc.collect()
        self.frames = []

        self.loader = FrameLoader(path)
        self.loader.progress.connect(self.on_loading)
        self.loader.finished.connect(self.on_loaded)
        self.loader.error.connect(self.on_error)
        self.loader.start()

    # フレーム単位完了通知
    def on_loading(self, frames, count, total, fps):
        self.frames = frames
        self.loaded_frame = count + 1
        self.total_frame = total
        self.fps = fps
        self.waittime = int(1000.0 / fps)
        if count == 0:
            self.playing = False
            self.timer.stop()
            fr = self.frames[0]
            h, w, _ = fr.shape
            qimg =  QImage(w, h, QImage.Format_RGB888)
            color = QColor(128, 128, 128)
            qimg.fill(color)
            qimg = self.draw_text_on_image_center(qimg, "not loaded", 32)
            self.dummyimage = qimg

        self.update_frame()
        self.update()

    # エラー通知
    def on_error(self, msg):
        self.info_label.setText("Error: " + msg)

    # 完了通知
    def on_loaded(self, frames):
        self.frames = frames
        self.update_frame()

    # --------------------------------
    # 表示更新
    # --------------------------------
    def update_frame(self):
        if not self.frames:
            return

        if self.current_frame < self.loaded_frame:
            fr = self.frames[self.current_frame]
            h, w, _ = fr.shape
            qimg = QImage(fr.data, w, h, 3 * w, QImage.Format_RGB888)
        else:
            # まだ読み込みが完了していないフレームはダミー表示
            qimg = self.dummyimage
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(scaled)

        txtlen = len(str(self.total_frame))
        fileinfo = f"File:{(self.current_index + 1):>{len(str(len(self.playlist)))}}/{len(self.playlist)}"
        frameinfo = f"Frame:{(self.current_frame + 1):>{txtlen}}/{self.total_frame}"
        loadinfo = f"Loaded."
        if (self.loaded_frame != self.total_frame):
            loadinfo = f"Load:{self.loaded_frame:>{txtlen}}/{self.total_frame}"
        self.info_label.setText(f"{fileinfo}, {frameinfo}, {loadinfo}")

    # 再生処理
    def next_frame(self):
        if not self.frames:
            return
        self.current_frame = (self.current_frame + 1) % self.total_frame
        self.update_frame()
        self.timer.start(int(self.waittime))

    # --------------------------------
    # イベント処理
    # --------------------------------
    # ウインドウサイズ変更
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_frame()
    # 終了時
    def closeEvent(self, event):
        self.save_settings()
        super().closeEvent(event)

    # --------------------------------
    # 入力処理
    # --------------------------------
    # キーボード
    def keyPressEvent(self, e):
        key = e.key()

        # スペース：再生/停止
        if key == Qt.Key_Space:
            self.toggle_play()
        # 左、A：次のフレーム
        elif key in (Qt.Key_Left, Qt.Key_A):
            self.prev_frame()
        # 右、D：前のフレーム
        elif key in (Qt.Key_Right, Qt.Key_D):
            self.next_frame_manual()
        # , Q：次の動画
        elif key in (Qt.Key_Comma, Qt.Key_Q):
            self.prev_movie()
        # . E：前の動画
        elif key in (Qt.Key_Period, Qt.Key_E):
            self.next_movie()
        # 上 W：保存
        elif key in (Qt.Key_Up, Qt.Key_W):
            self.save_frame()
        # F：フィット表示（動画サイズにウインドウを合わせる）
        elif key == Qt.Key_F:
            self.fit_window()

    # マウスボタン
    def mousePressEvent(self, e):
        test = e.button()
        # 左：再生/停止
        if e.button() == Qt.LeftButton:
            self.toggle_play()
        # 右：保存
        elif e.button() == Qt.RightButton:
            self.save_frame()
        # 戻る：Back
        elif e.button() == Qt.XButton1:
            self.prev_movie()
        # 進む：Forward
        elif e.button() == Qt.XButton2:
            self.next_movie()
        # 中：フィット表示（動画サイズにウインドウを合わせる）
        elif e.button() == Qt.MiddleButton:
            self.fit_window()

    # マウスホイール
    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if delta > 0:
            self.prev_frame()
        else:
            self.next_frame_manual()

    # --------------------------------
    # 動作機能
    # --------------------------------
    # 再生／停止
    def toggle_play(self):
        if not self.frames:
            return
        self.playing = not self.playing
        if self.playing:
            self.timer.start(int(self.waittime))
        else:
            self.timer.stop()
    # 停止
    def stop_play(self):
        if not self.frames:
            return
        self.playing = False
        self.timer.stop()

    # 次のフレーム
    def next_frame_manual(self):
        if not self.frames:
            return
        self.stop_play()
        if self.current_frame == (self.total_frame - 1):
            return
        self.current_frame = (self.current_frame + 1) % self.total_frame
        self.update_frame()
    # 前のフレーム
    def prev_frame(self):
        if not self.frames:
            return
        self.stop_play()
        if self.current_frame == 0:
            return
        self.current_frame = (self.current_frame - 1) % self.total_frame
        self.update_frame()

    # 次の動画
    def next_movie(self):
        if self.current_index + 1 < len(self.playlist):
            self.current_index += 1
            self.load_current()
    # 前の動画
    def prev_movie(self):
        if self.current_index > 0:
            self.current_index -= 1
            self.load_current()

    # フィット表示（実寸サイズにウインドウを合わせる）
    def fit_window(self):
        if self.frames:
            fr = self.frames[self.current_frame]
            h, w, _ = fr.shape
            self.resize(w, h + 40)
        self.update_frame()

    # サウンド再生
    def play_wave(self, file_name):
        if not file_name: return
        file_path = f"{self.pydir}/{file_name}"
        pvsubfunc.play_wave(file_path)

    # ダミー画像用テキスト描画
    def draw_text_on_image_center(self, image: QImage, text: str, font_size: int) -> QImage:
        if image.isNull():
            return image

        painter = QPainter(image)
        font = QFont("Arial", font_size, QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        metrics = QFontMetrics(font)
        text_width = metrics.horizontalAdvance(text)
        text_height = metrics.height()
        text_descent = metrics.descent()
        image_width = image.width()
        image_height = image.height()
        x = (image_width - text_width) // 2
        y = (image_height + text_height) // 2 - text_descent
        painter.drawText(x, y, text)
        painter.end()
        return image

    # --------------------------------
    # 保存
    # --------------------------------
    def save_frame(self):
        if not self.frames:
            return

        srcfname = self.playlist[self.current_index]
        path = os.path.dirname(srcfname)
        base = os.path.splitext(os.path.basename(srcfname))[0]
        # シーク動作にあわせてframe番号を1オリジンに変更
        fname = f"{base}_frm{self.current_frame + 1:04d}.png"
        fullfname = os.path.join(path, fname)

        if self.current_frame >= self.loaded_frame:
            #まだロード出来ていないフレームの保存は失敗扱い
            self.play_wave(self.soundNG)
            self.info_label.setText(f"not loaded frame: {fullfname}")
            return

        try:
            fr = self.frames[self.current_frame]
            img = Image.fromarray(fr)
            img.save(fullfname)

            self.play_wave(self.soundOK)
            self.info_label.setText(f"Saved: {fullfname}")
        except Exception as e:
            self.play_wave(self.soundNG)
            self.info_label.setText(f"error: {fullfname}")

    # --------------------------------
    # 設定データ
    # --------------------------------
    def load_settings(self):
        geox = pvsubfunc.read_value_from_config(SETTINGS_FILE, GEOMETRY_X)
        geoy = pvsubfunc.read_value_from_config(SETTINGS_FILE, GEOMETRY_Y)
        geow = pvsubfunc.read_value_from_config(SETTINGS_FILE, GEOMETRY_W)
        geoh = pvsubfunc.read_value_from_config(SETTINGS_FILE, GEOMETRY_H)
        if not any(val is None for val in [geox, geoy, geow, geoh]):
            self.setGeometry(geox, geoy, geow, geoh)

        self.soundOK = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_FILE_OK, DEF_SOUND_OK)
        self.soundNG = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_FILE_NG, DEF_SOUND_NG)

    def save_settings(self):
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_X, self.geometry().x())
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_Y, self.geometry().y())
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_W, self.geometry().width())
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_H, self.geometry().height())

        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_FILE_OK, self.soundOK)
        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_FILE_NG, self.soundNG)

# ---------------------------------------------------------
# 起動
# ---------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
