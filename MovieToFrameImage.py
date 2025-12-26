import sys, os, gc
import numpy as np
import imageio.v2 as imageio
from PIL import Image

from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap, QColor, QPainter, QFont, QFontMetrics
from PyQt5.QtWidgets import (
    QLabel, QMainWindow, QApplication, QSizePolicy,
    QWidget, QStatusBar, QVBoxLayout, QHBoxLayout
)

import shutil
import pvsubfunc

DEF_SOUND_BEEP = "PromptViewer_beep.wav"
DEF_SOUND_FCOPY_OK = "PromptViewer_filecopyok.wav"
DEF_SOUND_F_CANSEL = "PromptViewer_filecansel.wav"
DEF_SOUND_MOVE_TOP = "PromptViewer_movetop.wav"
DEF_SOUND_MOVE_END = "PromptViewer_moveend.wav"
APP_WIDTH = 320
APP_HEIGHT = 320
APP_BGCOLOR = QColor(32, 32, 32)

# ファイルコピー先のディレクトリ
DEF_IMAGE_FCOPY_DIR = "W:/_temp/ai"
# 最大フレーム数（これを超えるとエラー扱い）
DEF_MAX_FRAME = 5000

# アプリ名称
WINDOW_TITLE = "MovieToFrameImage 0.2.1"
# 設定ファイル
SETTINGS_FILE = "MovieToFrameImage.json"
# 設定ファイルのキー名
GEOMETRY_X = "geometry-x"
GEOMETRY_Y = "geometry-y"
GEOMETRY_W = "geometry-w"
GEOMETRY_H = "geometry-h"
SOUND_BEEP = "sound-beep"
SOUND_FCOPY_OK = "sound-fcopy-ok"
SOUND_F_CANSEL = "sound-f-cansel"
SOUND_MOVE_TOP = "sound-move-top"
SOUND_MOVE_END = "sound-move-end"
IMAGE_FCOPY_DIR = "image-fcopy-dir"

# -------------------------------
# フレーム読み込みスレッド
# -------------------------------
class FrameLoader(QThread):
    progress = pyqtSignal(list, int, int, float)    # frames, 現在フレーム数, 総フレーム数, fps
    finished = pyqtSignal(list)                     # frames
    error = pyqtSignal(str, int, int)                    # error msg, type, val

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
                if total_frames > DEF_MAX_FRAME:
                    self.error.emit("Too many frames", 2, total_frames)
                    return

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
                if total_frames > DEF_MAX_FRAME:
                    self.error.emit("Too many frames", 2, total_frames)
                    return
                # mp4も固定のフレームレートとして処理する（手抜き）
                framerate = reader.get_meta_data().get("fps", 15.0)

                for idx, fr in enumerate(reader):
                    frames.append(fr)
                    self.progress.emit(frames, idx, total_frames, framerate)

                reader.close()
                self.finished.emit(frames)

            else:
                self.error.emit("Unsupported format", 1, 0)

        except Exception as e:
            self.error.emit(str(e), 0, 0)

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
        self.setStyleSheet(f"background-color: {APP_BGCOLOR.name()};")
        self.setAcceptDrops(True)

        # 状態管理
        self.playlist = []
        self.current_index = -1
        self.loader = None
        self.frames = []
        self.durations = []
        self.fps = 0.0
        self.current_filename = ""
        self.current_frame = 0
        self.total_frame = 0
        self.loaded_frame = 0
        self.waittime = int(1000.0 / 15)
        self.waitplay = self.waittime
        self.playing = False
        self.playmode = 1      # 0:stop, 1:1x, 2:2x, 3:4x, 4:8x
        self.dummyimage = None

        self.pydir = os.path.dirname(os.path.abspath(__file__))
        self.soundBeep = DEF_SOUND_BEEP
        self.soundFileCopyOK = DEF_SOUND_FCOPY_OK
        self.soundFileCansel = DEF_SOUND_F_CANSEL
        self.soundMoveTop = DEF_SOUND_MOVE_TOP
        self.soundMoveEnd = DEF_SOUND_MOVE_END
        self.imageFileCopyDir = ""

        #設定ファイルがあれば読み込み
        if os.path.exists(SETTINGS_FILE):
            self.load_settings()

        # タイマー
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.next_frame)

        # UI
        self.centralWidget = QWidget()
        self.setCentralWidget(self.centralWidget)
        self.layout = QVBoxLayout(self.centralWidget)
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.label.setMinimumSize(APP_WIDTH, APP_HEIGHT)

        self.layout.addWidget(self.label)

        self.statusBar = QStatusBar()
        self.statusBar.setStyleSheet("color: white; font-size: 14px; background-color: #31363b;")
        self.setStatusBar(self.statusBar)
        self.showStatusMes("動画ファイルかディレクトリをドロップしてください")

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

        target = paths[0]   # 先頭ファイルのみ対象
        targetpath = target
        targetfile = ""
        if os.path.isfile(target):
            if not os.path.isfile(target) and f.lower().endswith((".mp4", ".webp")):
                self.showStatusMes(f"not support file [{target}]")
                self.play_wave(self.soundBeep)
                return
            targetpath = os.path.dirname(target)
            targetfile = os.path.basename(target)

        collected = []
        # ディレクトリ → 直下の mp4/webp を対象にする
        for f in os.listdir(targetpath):
            full = os.path.join(targetpath, f)
            if os.path.isfile(full) and f.lower().endswith((".mp4", ".webp")):
                collected.append(full)
        # 対象がない
        if not collected:
            self.showStatusMes(f"not exist support file [{targetpath}]")
            self.play_wave(self.soundBeep)
            return

        # playlist を入れ替え
        self.playlist = collected
        self.current_index = 0
        # fileドロップだった場合にはindexをファイルまで進める
        if targetfile != "":
            for file in self.playlist:
                if os.path.basename(file) == targetfile:
                    break
                self.current_index = self.current_index + 1
        if self.current_index >= len(self.playlist):
            self.current_index = 0  # 保険処理

        self.load_current()  # ←必ずロード
        self.raise_()
        self.activateWindow()

    # --------------------------------
    # 動画読み込み開始
    # --------------------------------
    # ロード
    def load_current(self):
        self.current_filename = self.playlist[self.current_index]
        fileinfo = f"[{(self.current_index + 1):>{len(str(len(self.playlist)))}}/{len(self.playlist)}]"
        self.setWindowTitle(f"{fileinfo} {os.path.basename(self.current_filename)}")
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

        self.loader = FrameLoader(self.current_filename)
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
            self.stop_play()
            fr = self.frames[0]
            h, w, _ = fr.shape
            qimg =  QImage(w, h, QImage.Format_RGB888)
            color = APP_BGCOLOR
            qimg.fill(color)
            qimg = self.draw_text_on_image_center(qimg, "now loading...", 32)
            self.dummyimage = qimg

        self.update_frame()

        if count == 0:
            self.start_play()

    # エラー通知
    def on_error(self, msg, errtype, errval):
        self.showStatusMes(f"Error: {msg}")
        if errtype == 0:
            self.show_image_message(f"例外が発生しました", 16)
        elif errtype == 1:
            self.show_image_message(f"表示できないファイルです", 16)
        elif errtype == 2:
            self.show_image_message(f"フレーム数が多すぎます {errval} / 最大{DEF_MAX_FRAME}まで", 16)

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
        loadinfo = f"Loaded"
        if (self.loaded_frame != self.total_frame):
            loadinfo = f"Load:{self.loaded_frame:>{txtlen}}/{self.total_frame}"
        speedinfo = f"play {self.get_speed(self.playmode)}x"
        if self.playing == False:
            if self.playmode == 0:
                speedinfo = f"stop"
            else:
                speedinfo = f"pause {self.get_speed(self.playmode)}x"

        self.showStatusMes(f"{fileinfo}, {frameinfo}, {loadinfo}, {speedinfo}")

    # 再生処理
    def next_frame(self):
        self.next_frame_manual(False)

    # ファイルのロード中画面表示
    # ※小さいwebpの場合ちらついてみえるが、大きいファイルの時はこれがないと固まって見える
    def show_image_message(self, text, fontsize = 24):
        h = self.label.size().height()
        w = self.label.size().width()
        qimg =  QImage(w, h, QImage.Format_RGB888)
        color = APP_BGCOLOR
        qimg.fill(color)
        qimg = self.draw_text_on_image_center(qimg, text, fontsize)
        pix = QPixmap.fromImage(qimg)
        scaled = pix.scaled(self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(scaled)
        self.label.repaint()

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

        # スペース：再生速度変更
        if key == Qt.Key_Space:
            self.change_playspeed()
        # 左、A：次のフレーム
        elif key in {Qt.Key_Left, Qt.Key_A}:
            self.prev_frame()
        # 右、D：前のフレーム
        elif key in {Qt.Key_Right, Qt.Key_D}:
            self.next_frame_manual()
        # , Q：次の動画
        elif key in {Qt.Key_Comma, Qt.Key_Q}:
            self.prev_movie()
        # . E：前の動画
        elif key in {Qt.Key_Period, Qt.Key_E}:
            self.next_movie()
        # 上 W：保存
        elif key in {Qt.Key_Up, Qt.Key_W}:
            self.stop_play()    # フレーム保存時はpause状態にする
            self.save_frame()
        # F：フィット表示（動画サイズにウインドウを合わせる）
        elif key == Qt.Key_F:
            self.fit_window(1.0)
        elif key == Qt.Key_1:
            self.fit_window(1.0)
        elif key == Qt.Key_2:
            self.fit_window(2.0)
        elif key == Qt.Key_3:
            self.fit_window(3.0)
        elif key == Qt.Key_0:
            self.fit_window(0.5)
        # R Return：指定フォルダへコピー
        elif key in {Qt.Key_R, Qt.Key_Return}:
            self.copyImageFile(self.current_filename, self.imageFileCopyDir)
        # ESC / \：終了
        elif key in {Qt.Key_Escape, Qt.Key_Slash, Qt.Key_Backslash}:
            self.appexit()

    # マウスボタン
    def mousePressEvent(self, e):
        test = e.button()
        # 左：再生速度変更
        if e.button() == Qt.LeftButton:
            self.change_playspeed()
        # 右：保存
        elif e.button() == Qt.RightButton:
            self.stop_play()    # フレーム保存時はpause状態にする
            self.save_frame()
        # 戻る：Back
        elif e.button() == Qt.XButton1:
            self.prev_movie()
        # 進む：Forward
        elif e.button() == Qt.XButton2:
            self.next_movie()
        # 中：フィット表示（動画サイズにウインドウを合わせる）
        elif e.button() == Qt.MiddleButton:
            self.fit_window(1.0)

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
    # 再生速度変更
    def change_playspeed(self):
        # 停止中なら再開のみ
        if self.playing == False and self.playmode != 0:
            self.start_play()
            return
        self.playmode = (self.playmode + 1) % 5   # 0 to 4
        if self.playmode == 0:
            self.stop_play()
        else:
            self.start_play()
        self.update_frame()

    # 再生
    def start_play(self):
        if not self.frames:
            return
        self.playing = True
        self.waitplay = self.waittime / self.get_speed(self.playmode)
        self.timer.start(int(self.waitplay))
    def get_speed(self, mode):
        res = 1
        if mode > 1:
            res = 2 ** (mode - 1)
        return res
    # 停止
    def stop_play(self):
        if not self.frames:
            return
        self.playing = False
        self.timer.stop()

    # 次のフレーム
    def next_frame_manual(self, isManual=True):
        if not self.frames:
            return
        if isManual:
            self.stop_play()
        if self.current_frame == (self.total_frame - 1):
            self.play_wave(self.soundMoveTop)
        self.current_frame = (self.current_frame + 1) % self.total_frame
        self.update_frame()
        if not isManual:
            self.timer.start(int(self.waitplay))
    # 前のフレーム
    def prev_frame(self):
        if not self.frames:
            return
        self.stop_play()
        if self.current_frame == 0:
            self.play_wave(self.soundMoveEnd)
        self.current_frame = (self.current_frame - 1) % self.total_frame
        self.update_frame()

    # 次の動画
    def next_movie(self):
        self.current_index = (self.current_index + 1) % len(self.playlist)
        self.move_func()
    # 前の動画
    def prev_movie(self):
        self.current_index = (self.current_index - 1) % len(self.playlist)
        self.move_func()
    # 動画の移動表示
    def move_func(self):
        self.statusBar.showMessage("file loading...")
        self.show_image_message("file loading...")
        self.load_current()

    # フィット表示（実寸サイズにウインドウを合わせる）
    def fit_window(self, mag):
        if self.frames:
            fr = self.frames[self.current_frame]
            h, w, _ = fr.shape
            w = int(w * mag)
            h = int(h * mag)
            #self.resize(w, h)
            self.resize_window_to_fit_image(w, h)
        self.update_frame()
    def resize_window_to_fit_image(self, img_w, img_h):
        # 現在のウインドウフレームと centralWidget の差分（フレーム幅）を取得
        frame_w = self.width() - self.centralWidget.width()
        frame_h = self.height() - self.centralWidget.height()
        # centralWidget に画像サイズをぴったり合わせたフレームサイズを算出
        new_w = img_w + frame_w
        new_h = img_h + frame_h
        self.resize(new_w, new_h)

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
        painter.setPen(QColor(200, 200, 200))
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

    def appexit(self):
        self.close()

    def showStatusMes(self, mes):
        self.statusBar.showMessage(f"{mes}")

    # --------------------------------
    # コピー処理
    # --------------------------------
    def copyImageFile(self, srcfile, destdir):
        # なぜかソースファイルがない
        if not os.path.exists(srcfile):
            self.showStatusMes(f"not exist [{srcfile}]")
            self.play_wave(self.soundBeep)
            return

        destfile = os.path.join(destdir, os.path.basename(srcfile)).replace("\\", "/")
        # コピー先から削除
        if os.path.exists(destfile):
            os.remove(destfile)
            self.showStatusMes(f"copy cansel [{destfile}]")
            self.play_wave(self.soundFileCansel)
            return

        # コピー処理
        shutil.copy2(srcfile, destdir)
        if os.path.exists(destfile):
            self.showStatusMes(f"copyed [{destfile}]")
            self.play_wave(self.soundFileCopyOK)
        else:
            self.showStatusMes(f"copy error [{destfile}]")
            self.play_wave(self.soundBeep)

    # --------------------------------
    # 保存
    # --------------------------------
    def save_frame(self):
        if not self.frames:
            return

        srcfname = self.current_filename
        path = os.path.dirname(srcfname)
        base = os.path.splitext(os.path.basename(srcfname))[0]
        # シーク動作にあわせてframe番号を1オリジンに変更
        fname = f"{base}_frm{self.current_frame + 1:04d}.png"
        fullfname = os.path.join(path, fname)

        if self.current_frame >= self.loaded_frame:
            #まだロード出来ていないフレームの保存は失敗扱い
            self.play_wave(self.soundBeep)
            self.showStatusMes(f"not loaded frame: {fullfname}")
            return

        # 同名のファイルがすでに存在していれば削除のみ実行
        if os.path.exists(fullfname):
            os.remove(fullfname)
            self.showStatusMes(f"Deleted: [{fullfname}]")
            self.play_wave(self.soundFileCansel)
            return

        try:
            fr = self.frames[self.current_frame]
            img = Image.fromarray(fr)
            img.save(fullfname)

            self.play_wave(self.soundFileCopyOK)
            self.showStatusMes(f"Saved: {fullfname}")
        except Exception as e:
            self.play_wave(self.soundBeep)
            self.showStatusMes(f"error: {fullfname}")

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

        self.soundBeep = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_BEEP, DEF_SOUND_BEEP)
        self.soundFileCopyOK = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_FCOPY_OK, DEF_SOUND_FCOPY_OK)
        self.soundFileCansel = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_F_CANSEL, DEF_SOUND_F_CANSEL)
        self.soundMoveTop = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_MOVE_TOP, DEF_SOUND_MOVE_TOP)
        self.soundMoveEnd = pvsubfunc.read_value_from_config(SETTINGS_FILE, SOUND_MOVE_END, DEF_SOUND_MOVE_END)
        self.imageFileCopyDir = pvsubfunc.read_value_from_config(SETTINGS_FILE, IMAGE_FCOPY_DIR)
        if not self.imageFileCopyDir:
            pvsubfunc.write_value_to_config(SETTINGS_FILE, IMAGE_FCOPY_DIR, DEF_IMAGE_FCOPY_DIR)

    def save_settings(self):
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_X, self.geometry().x())
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_Y, self.geometry().y())
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_W, self.geometry().width())
        pvsubfunc.write_value_to_config(SETTINGS_FILE, GEOMETRY_H, self.geometry().height())

        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_BEEP, self.soundBeep)
        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_FCOPY_OK, self.soundFileCopyOK)
        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_F_CANSEL, self.soundFileCansel)
        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_MOVE_TOP, self.soundMoveTop)
        pvsubfunc.write_value_to_config(SETTINGS_FILE, SOUND_MOVE_END, self.soundMoveEnd)

# ---------------------------------------------------------
# 起動
# ---------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())
