"""Visible request/preview activity without invented completion percentages."""
import time
from PySide6.QtCore import QTimer,Signal
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QLabel,QProgressBar,QPushButton


class SearchActivity(QFrame):
    stop_requested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('searchActivity')
        self.setStyleSheet('#searchActivity {background:#20212e;border:1px solid #454064;border-radius:9px;} #searchActivity QLabel {color:#ebe7fa;} QProgressBar {background:#343044;border:0;border-radius:2px;} QProgressBar::chunk {background:#b8a4ff;}')
        layout=QVBoxLayout(self);layout.setContentsMargins(16,10,16,10)
        row=QHBoxLayout();self.heading=QLabel();self.heading.setWordWrap(True);row.addWidget(self.heading,1)
        self.stop=QPushButton('검색 중단');self.stop.clicked.connect(self.stop_requested.emit);row.addWidget(self.stop)
        layout.addLayout(row)
        self.bar=QProgressBar();self.bar.setRange(0,0);self.bar.setTextVisible(False);self.bar.setFixedHeight(5);layout.addWidget(self.bar)
        self.sources_layout=QHBoxLayout();layout.addLayout(self.sources_layout)
        self.previews=QLabel('미리보기: 검색 결과를 기다리는 중');layout.addWidget(self.previews)
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick)
        self.session=None;self.active=False;self.sources={};self.started=0;self.preview_pending=0
        self.hide()

    def begin(self,session,names):
        if session!=self.session:
            self.preview_pending=0;self.previews.setText('미리보기: 검색 결과를 기다리는 중')
            while self.sources_layout.count():
                item=self.sources_layout.takeAt(0)
                if item.widget():item.widget().deleteLater()
            self.sources={}
            for identity,name in names.items():
                label=QLabel();label.setWordWrap(True);self.sources_layout.addWidget(label)
                self.sources[identity]=dict(name=name,label=label,state='queued',count=0,started=time.monotonic())
        self.session=session;self.active=True;self.started=time.monotonic()
        self.stop.setEnabled(True);self.bar.show();self.show();self.timer.start();self.tick()

    def update_event(self,session,event):
        if session!=self.session or not self.active:return
        row=self.sources.get(event.get('source'))
        if row is None:return
        row['state']=event['state'];row['started']=time.monotonic()
        if event['state']=='complete':row['count']+=event.get('added',0)
        row['label'].setToolTip(event.get('error',''))
        if event['state']=='failed':row['error']=event.get('error','')
        self.tick()

    def tick(self):
        count=sum(row['count'] for row in self.sources.values())
        if self.active:self.heading.setText(f'검색 중 · 경과 {int(time.monotonic()-self.started)}초 · 누적 새 후보 {count:,}개')
        for row in self.sources.values():
            state=row['state'];seconds=int(time.monotonic()-row['started'])
            if state=='queued':text='순서 대기';color='#b8b4d7'
            elif state=='running':text=('응답 지연' if seconds>=30 else '응답 대기')+f' · {seconds}초';color='#b9a4ff'
            elif state=='complete':
                text=f'✓ 새 후보 {row["count"]:,}개' if row['count'] else '검색 결과 없음 · 0개'
                color='#87cfb0' if row['count'] else '#b8b4d7'
            elif state=='failed':text='⚠ '+row.get('error','연결 실패')[:110];color='#f0ae88'
            elif state=='idle':text='이번 요청 없음';color='#b8b4d7'
            else:text='중단';color='#b8b4d7'
            row['label'].setText(row['name']+' · '+text);row['label'].setStyleSheet('color:'+color)

    def finish(self,session,message,cancelled=False):
        if session!=self.session:return
        self.active=False;self.timer.stop();self.stop.setEnabled(False)
        self.heading.setText(message)
        for row in self.sources.values():
            if row['state'] in ('queued','running'):row['state']='stopped' if cancelled else 'idle'
        self.bar.setVisible(self.preview_pending>0);self.tick()

    def preview_progress(self,ready,pending,failed):
        self.preview_pending=pending
        self.previews.setText(f'화면 주변 미리보기 · 준비 {ready:,}개 · 대기 {pending:,}개 · 실패 {failed:,}개')
        self.bar.setVisible(self.active or pending>0)

    def dismiss(self):
        self.active=False;self.timer.stop();self.hide()
