import sys
import os
import numpy as np
import imageio.v2 as imageio
from PIL import Image

from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QLabel, QMainWindow, QApplication, QSizePolicy

TITLENAME = "MovieToFrameImage 0.1.0"

# -------------------------------
# フレーム読み込みスレッド
# -------------------------------
class FrameLoader(QThread):
    progress = pyqtSignal(int)        # 現在フレーム数
    finished = pyqtSignal(list, float, list)  # frames, fps, durations
    error = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            ext = os.path.splitext(self.path)[1].lower()

            if ext == ".mp4":
                reader = imageio.get_reader(self.path)
                fps = reader.get_meta_data().get("fps", 30.0)
                frames = []
                durations = []

                for idx, fr in enumerate(reader):
                    frames.append(fr)
                    durations.append(1.0 / fps)
                    self.progress.emit(idx + 1)

                reader.close()

                self.finished.emit(frames, fps, durations)
                return

            elif ext == ".webp":
                img = Image.open(self.path)
                frames = []
                durations = []
                idx = 0

                while True:
                    frame = np.array(img.convert("RGB"))
                    frames.append(frame)

                    # 1フレームの ms → 秒
                    dur_ms = img.info.get("duration", 100)
                    durations.append(dur_ms / 1000.0)

                    self.progress.emit(idx + 1)
                    idx += 1

                    try:
                        img.seek(img.tell() + 1)
                    except EOFError:
                        break

                # メタデータ fps = 平均値で算出（webp は固定fpsではないため）
                if len(durations) > 0:
                    fps = 1.0 / (sum(durations) / len(durations))
                else:
                    fps = 15.0

                self.finished.emit(frames, fps, durations)
                return

            else:
                self.error.emit("Unsupported format")

        except Exception as e:
            self.error.emit(str(e))


# -------------------------------
# メインウインドウ
# -------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(TITLENAME)
        self.setAcceptDrops(True)

        # 状態管理
        self.playlist = []
        self.current_index = -1
        self.frames = []
        self.durations = []
        self.fps = 0.0
        self.current_frame = 0
        self.playing = False

        # タイマー
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.next_frame)

        # UI
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setMinimumSize(256, 256)

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
        if self.current_index < 0 or self.current_index >= len(self.playlist):
            return

        path = self.playlist[self.current_index]
        self.setWindowTitle(path)

        self.loader = FrameLoader(path)
        self.loader.progress.connect(self.on_loading)
        self.loader.finished.connect(self.on_loaded)
        self.loader.error.connect(self.on_error)
        self.loader.start()

    # フレーム単位完了通知
    def on_loading(self, count):
        self.info_label.setText(f"Loading... {count} frames")

    # エラー通知
    def on_error(self, msg):
        self.info_label.setText("Error: " + msg)

    # 完了通知
    def on_loaded(self, frames, fps, durations):
        self.frames = frames
        self.fps = fps
        self.durations = durations
        self.current_frame = 0
        self.playing = False
        self.timer.stop()
        self.update_frame()
        self.info_label.setText(f"FPS: {fps:.2f}   Frames: {len(frames)}")

    # --------------------------------
    # 表示更新
    # --------------------------------
    def update_frame(self):
        if not self.frames:
            return

        fr = self.frames[self.current_frame]
        h, w, _ = fr.shape
        qimg = QImage(fr.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(scaled)
        self.info_label.setText(f"File : {self.current_index + 1} / {len(self.playlist)}, Frame: {self.current_frame + 1} / {len(self.frames)}")

    def next_frame(self):
        if not self.frames:
            return
        self.current_frame = (self.current_frame + 1) % len(self.frames)
        self.update_frame()

        # 次のフレームまでの duration を使用（webp対応）
        dur = self.durations[self.current_frame] * 1000
        self.timer.start(int(dur))

    # --------------------------------
    # 入力処理
    # --------------------------------
    # キーボード
    def keyPressEvent(self, e):
        key = e.key()

        # スペース：再生/停止
        if key == Qt.Key_Space:
            self.toggle_play()
        # 左
        elif key in (Qt.Key_Left, Qt.Key_A):
            self.prev_frame()
        # 右
        elif key in (Qt.Key_Right, Qt.Key_D):
            self.next_frame_manual()
        # , < / Back
        elif key in (Qt.Key_Comma, Qt.Key_Q):
            self.prev_movie()
        # . > / Forward
        elif key in (Qt.Key_Period, Qt.Key_E):
            self.next_movie()
        # 上：保存
        elif key in (Qt.Key_Up, Qt.Key_W):
            self.save_frame()
        # F フィット表示
        elif key == Qt.Key_F:
            # 実寸サイズにウインドウを合わせる
            if self.frames:
                fr = self.frames[self.current_frame]
                h, w, _ = fr.shape
                self.resize(w, h + 40)
            self.update_frame()

    # マウスボタン
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggle_play()
        elif e.button() == Qt.RightButton:
            self.save_frame()

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
            dur = self.durations[self.current_frame] * 1000
            self.timer.start(int(dur))
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
        if self.current_frame == (len(self.frames) - 1):
            return
        self.current_frame = (self.current_frame + 1) % len(self.frames)
        self.update_frame()
    # 前のフレーム
    def prev_frame(self):
        if not self.frames:
            return
        self.stop_play()
        if self.current_frame == 0:
            return
        self.current_frame = (self.current_frame - 1) % len(self.frames)
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

    # ウインドウサイズ変更
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_frame()

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
        fr = self.frames[self.current_frame]
        img = Image.fromarray(fr)
        img.save(fullfname)

        self.info_label.setText(f"Saved: {fullfname}")

# ---------------------------------------------------------
# 起動
# ---------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(800, 600)
    w.show()
    sys.exit(app.exec_())
