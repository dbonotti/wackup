import sqlite3
import json
import os
import sys
import shutil
import threading
import subprocess
import time
import re
import csv
import io
import base64
import mimetypes
import webbrowser
import traceback
import html
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime

import db_manager
import media_manager
import config
from config import (
    BASE_DIR, DATA_DIR, DB_FILE, MEDIA_DIR, BACKUP_DIR, LOG_DIR,
    SYNC_LOG_PATH, ensure_data_dirs, migrate_legacy_data
)

PORT = 8080

def append_sync_log(entry_text):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(SYNC_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry_text + "\n")
    except Exception:
        pass

HTML_UI = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WAckup - Visor</title>
  <link rel="icon" type="image/jpeg" href="/favicon.jpg">
  <style>
    :root {
      --bg-body: #111b21;
      --bg-app: #ffffff;
      --bg-sidebar: #ffffff;
      --bg-header: #f0f2f5;
      --bg-chat: #efeae2;
      --bg-hover: #f5f6f6;
      --bg-active: #f0f2f5;
      --bg-input: #f0f2f5;
      --bg-incoming: #ffffff;
      --bg-outgoing: #d9fdd3;
      --bg-badge: #ffffff;
      --bg-quote: rgba(0,0,0,0.05);
      --border-color: #e9edef;
      --border-strong: #d1d7db;
      --text-primary: #111b21;
      --text-secondary: #54656f;
      --text-muted: #667781;
      --text-time: #8696a0;
      --accent: #00a884;
    }

    body.dark {
      --bg-body: #0b141a;
      --bg-app: #111b21;
      --bg-sidebar: #111b21;
      --bg-header: #202c33;
      --bg-chat: #0b141a;
      --bg-hover: #202c33;
      --bg-active: #2a3942;
      --bg-input: #202c33;
      --bg-incoming: #202c33;
      --bg-outgoing: #005c4b;
      --bg-badge: #182229;
      --bg-quote: rgba(255,255,255,0.08);
      --border-color: #222d34;
      --border-strong: #2a3942;
      --text-primary: #e9edef;
      --text-secondary: #aebac1;
      --text-muted: #8696a0;
      --text-time: #8696a0;
      --accent: #00a884;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { 
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
      background-color: var(--bg-body); 
      height: 100vh; 
      display: flex; 
      justify-content: center; 
      align-items: center;
      color: var(--text-primary);
    }
    #app-container { display: flex; width: 100vw; height: 100vh; background-color: var(--bg-app); overflow: hidden; }

    /* Scrollbars modernos estilo WhatsApp */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(134,150,160,0.3); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(134,150,160,0.5); }

    /* Panel izquierdo (Sidebar) */
    #sidebar { width: 410px; min-width: 340px; border-right: 1px solid var(--border-color); display: flex; flex-direction: column; background: var(--bg-sidebar); }
    .sidebar-header { 
      height: 60px; 
      background: var(--bg-header); 
      display: flex; 
      align-items: center; 
      justify-content: space-between; 
      padding: 0 14px; 
      font-weight: 600; 
      font-size: 15px;
      color: var(--text-primary); 
      border-bottom: 1px solid var(--border-color); 
    }
    .brand-title { display: flex; align-items: center; gap: 8px; }
    .brand-logo { width: 26px; height: 26px; border-radius: 6px; object-fit: cover; flex-shrink: 0; }
    
    .sidebar-actions { display: flex; align-items: center; gap: 8px; }
    .menu-container { position: relative; display: flex; align-items: center; }
    .btn-kebab {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      border: none;
      background: transparent;
      color: var(--text-secondary);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s, color 0.15s;
    }
    .btn-kebab:hover, .btn-kebab.active { background: var(--bg-hover); color: var(--text-primary); }
    .btn-kebab svg { width: 20px; height: 20px; fill: currentColor; }

    .dropdown-menu {
      position: absolute;
      top: calc(100% + 6px);
      right: 0;
      min-width: 235px;
      background: var(--bg-sidebar);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      box-shadow: 0 4px 18px rgba(11, 20, 26, 0.24);
      padding: 6px 0;
      display: none;
      flex-direction: column;
      z-index: 1500;
      animation: menuFadeIn 0.14s ease-out;
    }
    .dropdown-menu.open { display: flex; }
    @keyframes menuFadeIn {
      from { opacity: 0; transform: translateY(-6px) scale(0.97); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    .dropdown-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 18px;
      font-size: 13.8px;
      font-weight: 400;
      color: var(--text-primary);
      background: none;
      border: none;
      width: 100%;
      text-align: left;
      cursor: pointer;
      transition: background 0.12s;
      white-space: nowrap;
    }
    .dropdown-item:hover { background: var(--bg-hover); }
    .dropdown-divider { height: 1px; background: var(--border-color); margin: 4px 0; }

    .sync-badge { 
      font-size: 11.5px; 
      padding: 4px 9px; 
      border-radius: 14px; 
      background: rgba(37, 211, 102, 0.15); 
      color: #25d366; 
      font-weight: 600; 
      display: flex; 
      align-items: center; 
      gap: 5px;
      cursor: pointer;
    }
    .sync-dot { width: 7px; height: 7px; border-radius: 50%; background: #25d366; }

    /* Selector de Cuentas Multi-Cuenta */
    .account-selector-container {
      position: relative;
      display: flex;
      align-items: center;
    }
    .account-badge {
      display: flex;
      align-items: center;
      gap: 5px;
      background: var(--bg-input);
      border: 1px solid var(--border-color);
      padding: 4px 10px;
      border-radius: 14px;
      font-size: 11.5px;
      font-weight: 600;
      color: var(--text-primary);
      cursor: pointer;
      user-select: none;
      transition: background 0.15s, border-color 0.15s;
      max-width: 140px;
    }
    .account-badge:hover {
      background: var(--bg-hover);
      border-color: var(--accent);
    }
    .account-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .account-arrow {
      font-size: 10px;
      color: var(--text-time);
      margin-left: 1px;
    }
    .account-dropdown {
      min-width: 220px;
      right: 0;
      top: calc(100% + 6px);
    }
    .account-dropdown-item {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 8px 14px;
      cursor: pointer;
      border-bottom: 1px solid var(--border-color);
      transition: background 0.12s;
    }
    .account-dropdown-item:last-child {
      border-bottom: none;
    }
    .account-dropdown-item:hover {
      background: var(--bg-hover);
    }
    .account-dropdown-item.active {
      background: rgba(0, 168, 132, 0.08);
      border-left: 3px solid var(--accent);
    }
    .account-item-title {
      font-weight: 600;
      font-size: 12.5px;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .account-item-subtitle {
      font-size: 11px;
      color: var(--text-time);
    }

    .search-section { padding: 8px 12px; background: var(--bg-sidebar); border-bottom: 1px solid var(--border-color); display: flex; flex-direction: column; gap: 6px; }
    .search-box-wrapper { display: flex; position: relative; align-items: center; }
    .search-box { 
      width: 100%; 
      padding: 8px 34px 8px 36px; 
      border-radius: 8px; 
      border: 1px solid var(--border-color); 
      background: var(--bg-input); 
      color: var(--text-primary);
      font-size: 14px; 
      outline: none; 
      transition: all 0.2s;
    }
    .search-box:focus { background: var(--bg-sidebar); border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
    .search-icon-svg { position: absolute; left: 10px; fill: var(--text-secondary); width: 16px; height: 16px; pointer-events: none; }
    .search-clear-btn {
      position: absolute;
      right: 8px;
      border: none;
      background: none;
      color: var(--text-secondary);
      cursor: pointer;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      display: none;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      line-height: 1;
      transition: background 0.15s, color 0.15s;
    }
    .search-clear-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
    
    .search-mode-tabs { display: flex; gap: 6px; font-size: 12px; }
    .tab-btn { 
      border: none; 
      background: none; 
      padding: 4px 10px; 
      cursor: pointer; 
      color: var(--text-secondary); 
      border-radius: 12px; 
      font-weight: 500; 
      transition: all 0.15s;
    }
    .tab-btn.active { background: var(--accent); color: #ffffff; font-weight: 600; }

    #chat-list, #search-results-list { flex: 1; overflow-y: auto; list-style: none; }
    .chat-item, .search-item { 
      display: flex; 
      align-items: center; 
      padding: 10px 16px; 
      cursor: pointer; 
      border-bottom: 1px solid var(--border-color); 
      transition: background 0.15s; 
    }
    .chat-item:hover, .search-item:hover { background: var(--bg-hover); }
    .chat-item.active { background: var(--bg-active); border-left: 4px solid var(--accent); }
    
    .chat-avatar { 
      width: 46px; 
      height: 46px; 
      border-radius: 50%; 
      display: flex; 
      align-items: center; 
      justify-content: center; 
      font-weight: 600; 
      color: #ffffff; 
      margin-right: 12px; 
      flex-shrink: 0; 
      font-size: 17px;
    }
    .chat-info { flex: 1; overflow: hidden; }
    .chat-header-row { display: flex; justify-content: space-between; align-items: baseline; }
    .chat-name { font-size: 15px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .chat-time { font-size: 11px; color: var(--text-time); flex-shrink: 0; margin-left: 6px; }
    .chat-preview { font-size: 13.5px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 3px; }

    /* Resultados Búsqueda FTS5 */
    .search-item { flex-direction: column; align-items: flex-start; gap: 4px; padding: 12px 16px; }
    .search-chat-title { font-size: 12.5px; color: var(--accent); font-weight: 600; }
    .search-snippet { font-size: 14px; color: var(--text-primary); line-height: 19px; }
    .search-snippet mark { background-color: #ffe082; color: #111b21; padding: 1px 3px; border-radius: 2px; font-weight: 600; }
    .search-date { font-size: 11px; color: var(--text-time); align-self: flex-end; }

    /* Panel derecho (Conversación) */
    #main-chat { flex: 1; display: flex; flex-direction: column; background: var(--bg-chat); position: relative; }
    #chat-header { 
      height: 60px; 
      background: var(--bg-header); 
      display: flex; 
      align-items: center; 
      justify-content: space-between;
      padding: 0 20px; 
      border-bottom: 1px solid var(--border-strong); 
      z-index: 2; 
    }
    .header-left { display: flex; align-items: center; }
    #header-info h3 { font-size: 16px; color: var(--text-primary); font-weight: 600; }
    #header-info p, .header-sub { font-size: 12px; color: var(--text-muted); margin-top: 2px; display: flex; align-items: center; gap: 8px; }
    .header-phone-row { display: inline-flex; align-items: center; gap: 7px; }
    .header-phone-text { color: var(--text-secondary); font-weight: 500; font-size: 12.5px; letter-spacing: 0.1px; user-select: text; }
    .btn-copy-phone {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: 1px solid var(--border-strong);
      background: var(--bg-sidebar);
      color: var(--text-secondary);
      border-radius: 10px;
      padding: 1px 7px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
      user-select: none;
    }
    .btn-copy-phone:hover { background: var(--bg-hover); border-color: var(--accent); color: var(--accent); }
    .btn-copy-phone.copied { background: rgba(0, 168, 132, 0.14); border-color: var(--accent); color: var(--accent); }
    .btn-copy-phone svg { width: 11px; height: 11px; fill: currentColor; flex-shrink: 0; }

    .header-actions { display: flex; align-items: center; gap: 8px; }
    .btn-chat-action {
      border: 1px solid var(--border-strong);
      background: var(--bg-sidebar);
      border-radius: 14px;
      padding: 5px 12px;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-primary);
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.15s;
    }
    .btn-chat-action:hover { background: var(--bg-hover); border-color: var(--accent); color: var(--accent); }

    /* Barra de filtros dentro del chat */
    #chat-filter-bar {
      background: var(--bg-sidebar);
      border-bottom: 1px solid var(--border-color);
      padding: 6px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 12px;
      gap: 10px;
      z-index: 1;
      flex-wrap: wrap;
    }
    .filter-pills { display: flex; gap: 6px; flex-wrap: wrap; }
    .filter-pill {
      border: 1px solid var(--border-color);
      background: var(--bg-input);
      padding: 3px 9px;
      border-radius: 12px;
      cursor: pointer;
      color: var(--text-secondary);
      font-weight: 500;
      transition: all 0.15s;
    }
    .filter-pill.active { background: var(--accent); color: #ffffff; border-color: var(--accent); font-weight: 600; }
    .filter-dates { display: flex; align-items: center; gap: 6px; color: var(--text-muted); }
    .date-input {
      border: 1px solid var(--border-color);
      background: var(--bg-input);
      padding: 3px 6px;
      border-radius: 6px;
      font-size: 11.5px;
      outline: none;
      color: var(--text-primary);
    }
    .date-input:focus { border-color: var(--accent); background: var(--bg-sidebar); }

    #messages-container { 
      flex: 1; 
      overflow-y: auto; 
      padding: 16px 5%; 
      display: flex; 
      flex-direction: column; 
      gap: 4px; 
    }

    .btn-load-more {
      align-self: center;
      border: 1px solid var(--border-strong);
      background: var(--bg-badge);
      color: var(--accent);
      font-size: 12px;
      font-weight: 600;
      padding: 6px 16px;
      border-radius: 16px;
      cursor: pointer;
      margin: 6px 0 12px 0;
      box-shadow: 0 1px 2px rgba(0,0,0,0.1);
      transition: all 0.15s;
    }
    .btn-load-more:hover { background: var(--bg-hover); }

    /* Burbujas de chat */
    .date-badge { 
      align-self: center; 
      background: var(--bg-badge); 
      color: var(--text-secondary); 
      font-size: 11.5px; 
      font-weight: 500; 
      padding: 5px 12px; 
      border-radius: 7px; 
      box-shadow: 0 1px 0.5px rgba(11,20,26,0.13); 
      margin: 12px 0 6px 0; 
      user-select: none;
    }
    .msg-bubble { 
      max-width: 65%; 
      padding: 6px 9px 6px 9px; 
      border-radius: 7.5px; 
      font-size: 14.2px; 
      line-height: 19px; 
      position: relative; 
      word-wrap: break-word; 
      box-shadow: 0 1px 0.5px rgba(11,20,26,0.13); 
      display: flex; 
      flex-direction: column; 
    }
    .msg-incoming { 
      align-self: flex-start; 
      background: var(--bg-incoming); 
      color: var(--text-primary); 
      border-top-left-radius: 0; 
    }
    .msg-outgoing { 
      align-self: flex-end; 
      background: var(--bg-outgoing); 
      color: var(--text-primary); 
      border-top-right-radius: 0; 
    }
    
    .msg-sender { 
      font-size: 12px; 
      font-weight: 600; 
      margin-bottom: 2px; 
    }
    .msg-forwarded { display: flex; align-items: center; gap: 4px; font-size: 12.2px; font-style: italic; color: var(--text-muted); margin-bottom: 2px; user-select: none; }
    .msg-forwarded svg { width: 14px; height: 14px; fill: currentColor; flex-shrink: 0; }
    .msg-quote {
      background: var(--bg-quote);
      border-left: 4px solid var(--accent);
      border-radius: 4px;
      padding: 4px 8px;
      margin-bottom: 4px;
      font-size: 12.5px;
      cursor: pointer;
      transition: background-color 0.15s ease, opacity 0.15s ease;
    }
    .msg-quote:hover {
      background: rgba(0, 168, 132, 0.18);
    }
    .msg-quote-author {
      font-weight: 600;
      font-size: 11.5px;
      color: var(--accent);
      margin-bottom: 1px;
    }
    .msg-quote-text {
      color: var(--text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 340px;
    }
    .msg-text { white-space: pre-wrap; font-size: 14.2px; word-break: break-word; }
    .msg-footer { 
      align-self: flex-end; 
      font-size: 10.5px; 
      color: var(--text-muted); 
      margin-top: 3px; 
      margin-left: 16px; 
      display: flex; 
      align-items: center; 
      gap: 3px;
    }
    .msg-check { color: #53bdeb; font-size: 12px; font-weight: bold; }

    /* Multimedia */
    .media-container { margin-bottom: 4px; border-radius: 6px; overflow: hidden; }
    .media-img { 
      max-width: 320px; 
      max-height: 320px; 
      object-fit: cover; 
      border-radius: 6px; 
      cursor: pointer; 
      display: block; 
      transition: opacity 0.15s;
    }
    .media-img:hover { opacity: 0.95; }
    
    /* Contenedor de audio con control de velocidad */
    .audio-wrapper {
      display: flex;
      align-items: center;
      gap: 6px;
      background: rgba(0,0,0,0.03);
      padding: 4px 6px;
      border-radius: 8px;
    }
    .media-audio { width: 250px; height: 36px; outline: none; }
    .btn-speed {
      border: 1px solid var(--border-strong);
      background: var(--bg-sidebar);
      border-radius: 10px;
      padding: 3px 6px;
      font-size: 10.5px;
      font-weight: 700;
      color: var(--text-secondary);
      cursor: pointer;
      user-select: none;
      transition: all 0.15s;
    }
    .btn-speed:hover { background: var(--bg-hover); color: var(--text-primary); }

    .media-video { max-width: 340px; border-radius: 6px; display: block; }
    .media-doc { 
      display: flex; 
      align-items: center; 
      gap: 10px; 
      padding: 8px 12px; 
      background: var(--bg-quote); 
      border-radius: 6px; 
      text-decoration: none; 
      color: var(--text-primary); 
      font-size: 13.5px; 
      transition: background 0.15s; 
    }
    .media-doc:hover { opacity: 0.9; }
    .media-doc-icon { font-size: 24px; }
    .media-expired {
      font-size: 12px;
      color: var(--text-time);
      font-style: italic;
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 3px 0;
    }

    /* Modal Visor Imagen */
    #image-modal { 
      display: none; 
      position: fixed; 
      z-index: 1000; 
      left: 0; 
      top: 0; 
      width: 100vw; 
      height: 100vh; 
      background: rgba(0,0,0,0.85); 
      align-items: center; 
      justify-content: center; 
      cursor: zoom-out; 
    }
    #image-modal img { max-width: 90vw; max-height: 90vh; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.6); }

    /* Modal de Backups y Multimedia */
    #backup-modal, #media-modal {
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0;
      top: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(0,0,0,0.5);
      align-items: center;
      justify-content: center;
    }
    .modal-card {
      background: var(--bg-sidebar);
      color: var(--text-primary);
      border-radius: 12px;
      width: 480px;
      max-width: 90vw;
      padding: 20px 24px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.3);
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .modal-card h3 { font-size: 17px; color: var(--text-primary); }
    .modal-card p { font-size: 13.5px; color: var(--text-secondary); line-height: 19px; }
    .backup-list { max-height: 200px; overflow-y: auto; list-style: none; border: 1px solid var(--border-color); border-radius: 8px; }
    .backup-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--border-color); font-size: 12.5px; }
    .backup-item:last-child { border-bottom: none; }
    .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .stat-box { background: var(--bg-input); border: 1px solid var(--border-color); padding: 10px 14px; border-radius: 8px; }
    .stat-box .stat-val { font-size: 20px; font-weight: 700; color: var(--accent); }
    .stat-box .stat-lbl { font-size: 12px; color: var(--text-secondary); }
    .modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
    .btn-modal-cancel { border: 1px solid var(--border-strong); background: var(--bg-sidebar); color: var(--text-primary); padding: 6px 14px; border-radius: 8px; cursor: pointer; font-size: 13px; }
    .btn-modal-confirm { border: none; background: var(--accent); color: #fff; padding: 6px 16px; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 13px; }

    /* Estado vacío */
    #empty-state { 
      flex: 1; 
      display: flex; 
      flex-direction: column; 
      align-items: center; 
      justify-content: center; 
      color: var(--text-muted); 
      background: var(--bg-header); 
      text-align: center;
      padding: 20px;
    }
    #empty-state h2 { color: var(--text-primary); margin-bottom: 8px; font-weight: 400; font-size: 26px; }
    #empty-state p { font-size: 14px; max-width: 480px; line-height: 20px; }

    /* Tarjeta de Primer Inicio / Onboarding */
    .onboarding-box {
      background: var(--bg-sidebar);
      border: 1px solid var(--border-strong);
      border-radius: 14px;
      padding: 26px 28px;
      max-width: 560px;
      width: 92%;
      text-align: left;
      box-shadow: 0 4px 20px rgba(0,0,0,0.08);
    }
    .onboarding-badge {
      display: inline-block;
      background: rgba(0, 168, 132, 0.15);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 12px;
      margin-bottom: 12px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .onboarding-box h3 { font-size: 20px; color: var(--text-primary); margin-bottom: 8px; }
    .onboarding-box p.onboarding-desc { font-size: 13.5px; color: var(--text-secondary); line-height: 19px; margin-bottom: 18px; }
    .onboarding-steps { display: flex; flex-direction: column; gap: 12px; }
    .onboarding-step {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      background: var(--bg-input);
      padding: 12px 14px;
      border-radius: 10px;
      border: 1px solid var(--border-color);
    }
    .step-num {
      width: 26px;
      height: 26px;
      background: var(--accent);
      color: #fff;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      font-weight: 700;
      flex-shrink: 0;
    }
    .step-content { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--text-primary); line-height: 18px; width: 100%; }
    .step-content strong { color: var(--text-primary); font-size: 13.5px; }
    .step-content span { color: var(--text-secondary); }
    .path-copy-row { display: flex; gap: 8px; margin-top: 6px; align-items: center; }
    .path-copy-input {
      flex: 1;
      padding: 6px 10px;
      font-size: 12px;
      border: 1px solid var(--border-strong);
      border-radius: 6px;
      background: var(--bg-sidebar);
      color: var(--text-primary);
      font-family: monospace;
    }
    .btn-copy-path {
      background: var(--accent);
      color: #fff;
      border: none;
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
    }
    .btn-copy-path:hover { opacity: 0.9; }

    /* Indicador en vivo de espera en onboarding */
    .onboarding-live-status {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 16px;
      padding: 10px 14px;
      background: rgba(0, 168, 132, 0.08);
      border: 1px solid rgba(0, 168, 132, 0.28);
      border-radius: 8px;
      font-size: 13px;
      color: var(--text-primary);
    }
    .live-pulse-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 8px var(--accent);
      flex-shrink: 0;
      animation: pulseDot 1.8s infinite ease-in-out;
    }
    @keyframes pulseDot {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.3; transform: scale(0.8); }
    }

    /* Notificación Toast */
    #toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: #111b21;
      color: #ffffff;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 13.5px;
      font-weight: 500;
      box-shadow: 0 4px 14px rgba(0,0,0,0.25);
      display: none;
      z-index: 2000;
      animation: fadeIn 0.2s ease;
    }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
  </style>
</head>
<body>
  <div id="app-container">
    <!-- Panel izquierdo (Sidebar) -->
    <div id="sidebar">
      <div class="sidebar-header">
        <div class="brand-title">
          <img class="brand-logo" src="/favicon.jpg" alt="" width="26" height="26">
          <span>WAckup</span>
        </div>
        <div class="sidebar-actions">
          <div class="account-selector-container" id="accountSelectorContainer" style="display: none;">
            <div class="account-badge" id="accountSelectorBtn" onclick="toggleAccountDropdown(event)" title="Cuenta activa. Haz clic para cambiar de cuenta.">
              <span class="account-icon">📱</span>
              <span class="account-label" id="currentAccountLabel">Cuenta</span>
              <span class="account-arrow" id="accountSelectorArrow" style="display: none;">▾</span>
            </div>
            <div class="dropdown-menu account-dropdown" id="accountDropdown"></div>
          </div>
          <div class="sync-badge" id="syncStatusBadge" onclick="openMediaModal()" title="Ver estado de sincronización y multimedia">
            <div class="sync-dot"></div>
            <span id="syncStatusText">Conectado</span>
          </div>
          <div class="menu-container">
            <button class="btn-kebab" id="globalMenuBtn" onclick="toggleDropdown('globalDropdown', this, event)" title="Menú de opciones globales">
              <svg viewBox="0 0 24 24"><path d="M12 7a2 2 0 1 0-.001-4.001A2 2 0 0 0 12 7zm0 2a2 2 0 1 0-.001 3.999A2 2 0 0 0 12 9zm0 6a2 2 0 1 0-.001 3.999A2 2 0 0 0 12 15z"/></svg>
            </button>
            <div class="dropdown-menu" id="globalDropdown">
              <button class="dropdown-item" onclick="closeAllDropdowns(); openMediaModal();">
                <span>🎬</span>
                <span>Estado de multimedia</span>
              </button>
              <button class="dropdown-item" onclick="closeAllDropdowns(); openBackupModal();">
                <span>💾</span>
                <span>Copias de seguridad (Backup)</span>
              </button>
              <button class="dropdown-item" onclick="closeAllDropdowns(); exportContactsCsv();">
                <span>📇</span>
                <span>Exportar contactos (.csv)</span>
              </button>
              <button class="dropdown-item" onclick="closeAllDropdowns(); openSyncLogs();">
                <span>📋</span>
                <span>Logs de sincronización</span>
              </button>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item" onclick="toggleTheme(); closeAllDropdowns();">
                <span id="themeMenuIcon">🌙</span>
                <span id="themeMenuText">Modo oscuro</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="search-section">
        <div class="search-box-wrapper">
          <svg class="search-icon-svg" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
          <input type="text" id="searchInput" class="search-box" placeholder="Buscar un chat o iniciar uno nuevo..." oninput="handleSearchInput()">
          <button type="button" id="searchClearBtn" class="search-clear-btn" onclick="clearSearchInput()" title="Limpiar texto de búsqueda">✕</button>
        </div>
        <div class="search-mode-tabs">
          <button class="tab-btn active" id="tabChats" onclick="setSearchMode('chats')">Chats</button>
          <button class="tab-btn" id="tabGlobal" onclick="setSearchMode('global')">Búsqueda Global (FTS5)</button>
        </div>
      </div>

      <ul id="chat-list"></ul>
      <ul id="search-results-list" style="display: none;"></ul>
    </div>

    <!-- Panel derecho (Chat) -->
    <div id="main-chat">
      <div id="chat-header" style="display: none;">
        <div class="header-left">
          <div class="chat-avatar" id="headerAvatar"></div>
          <div id="header-info">
            <h3 id="headerTitle">Contacto</h3>
            <p id="headerSub">Identificador</p>
          </div>
        </div>
        <div class="header-actions">
          <div class="menu-container">
            <button class="btn-kebab" id="chatMenuBtn" onclick="toggleDropdown('chatDropdown', this, event)" title="Opciones del chat">
              <svg viewBox="0 0 24 24"><path d="M12 7a2 2 0 1 0-.001-4.001A2 2 0 0 0 12 7zm0 2a2 2 0 1 0-.001 3.999A2 2 0 0 0 12 9zm0 6a2 2 0 1 0-.001 3.999A2 2 0 0 0 12 15z"/></svg>
            </button>
            <div class="dropdown-menu" id="chatDropdown">
              <button class="dropdown-item" onclick="closeAllDropdowns(); toggleChatFilterBar();">
                <span>📅</span>
                <span id="chatFilterMenuText">Buscar por fecha y multimedia</span>
              </button>
              <div class="dropdown-divider"></div>
              <button class="dropdown-item" onclick="closeAllDropdowns(); exportCurrentChat(false);">
                <span>📥</span>
                <span>Exportar chat a HTML (ligero)</span>
              </button>
              <button class="dropdown-item" onclick="closeAllDropdowns(); exportCurrentChat(true);">
                <span>📦</span>
                <span>Exportar autónomo (con multimedia)</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Barra de Filtros del Chat (desplegable desde el menú de 3 puntos) -->
      <div id="chat-filter-bar" style="display: none;">
        <div class="filter-pills">
          <span class="filter-pill active" onclick="setMediaFilter(null, this)">Todos</span>
          <span class="filter-pill" onclick="setMediaFilter('image', this)">📷 Fotos</span>
          <span class="filter-pill" onclick="setMediaFilter('audio', this)">🎤 Audios</span>
          <span class="filter-pill" onclick="setMediaFilter('document', this)">📄 Documentos</span>
          <span class="filter-pill" onclick="setMediaFilter('video', this)">🎬 Videos</span>
        </div>
        <div class="filter-dates">
          <span>Desde:</span>
          <input type="date" id="filterDateFrom" class="date-input" onchange="applyDateFilter()">
          <span>Hasta:</span>
          <input type="date" id="filterDateTo" class="date-input" onchange="applyDateFilter()">
          <button type="button" class="filter-pill" onclick="closeChatFilterBar()" title="Cerrar y limpiar filtros" style="margin-left: 4px;">✕ Cerrar</button>
        </div>
      </div>
      
      <div id="messages-container" style="display: none;"></div>

      <div id="empty-state">
        <h2>WAckup</h2>
        <p>Selecciona una conversación a la izquierda o utiliza el buscador indexado de texto completo.</p>
        <p id="dbStatsText" style="margin-top: 14px; font-size: 13px; color: var(--text-time);"></p>
      </div>
    </div>
  </div>

  <!-- Modal Visor Imagen -->
  <div id="image-modal" onclick="this.style.display='none'">
    <img id="modalImg" src="">
  </div>

  <!-- Modal de Gestión de Backups -->
  <div id="backup-modal" onclick="if(event.target===this) closeBackupModal()">
    <div class="modal-card">
      <h3>Copias de Seguridad de Base de Datos</h3>
      <p>Crea copias manuales puntuales de tu archivo SQLite sin interrumpir el funcionamiento del servidor.</p>
      <div class="backup-list" id="backupListContainer">
        <p style="padding: 14px; color: var(--text-time); text-align: center;">Cargando respaldos...</p>
      </div>
      <div class="modal-actions">
        <button class="btn-modal-cancel" onclick="closeBackupModal()">Cerrar</button>
        <button class="btn-modal-confirm" onclick="triggerManualBackup()">+ Crear Nuevo Backup</button>
      </div>
    </div>
  </div>

  <!-- Modal de Estado Multimedia -->
  <div id="media-modal" onclick="if(event.target===this) closeMediaModal()">
    <div class="modal-card">
      <h3>Estado de Descargas Multimedia</h3>
      <p>Los archivos multimedia se descargan y descifran automáticamente en segundo plano desde los servidores de WhatsApp.</p>
      <div class="stats-grid">
        <div class="stat-box"><div class="stat-val" id="statMediaDownloaded">0</div><div class="stat-lbl">Descargados</div></div>
        <div class="stat-box"><div class="stat-val" id="statMediaPending">0</div><div class="stat-lbl">En cola / Pendientes</div></div>
        <div class="stat-box"><div class="stat-val" id="statMediaFailed">0</div><div class="stat-lbl">Con error (Reintentables)</div></div>
        <div class="stat-box"><div class="stat-val" id="statMediaExpired">0</div><div class="stat-lbl">Expirados en CDN</div></div>
      </div>
      <div class="modal-actions">
        <button class="btn-modal-cancel" onclick="closeMediaModal()">Cerrar</button>
        <button class="btn-modal-confirm" onclick="retryFailedMedia()">🔄 Reintentar Fallidos</button>
      </div>
    </div>
  </div>

  <div id="toast"></div>

  <script>
    let currentAccountId = localStorage.getItem('wackup_selected_account') || null;
    let availableAccounts = [];
    let allChats = [];
    let currentMode = 'chats';
    let searchDebounceTimer = null;
    let selectedChatId = null;
    let activeMediaFilter = null;
    let currentMessages = [];
    let filteredMessages = [];
    let renderedCount = 0;
    const PAGE_SIZE = 300;
    let lastSyncData = {};

    function withAccount(url) {
      if (!currentAccountId) return url;
      const sep = url.includes('?') ? '&' : '?';
      return `${url}${sep}account_id=${encodeURIComponent(currentAccountId)}`;
    }

    const AVATAR_COLORS = [
      '#00a884', '#0284c7', '#7c3aed', '#db2777', 
      '#ea580c', '#16a34a', '#059669', '#4f46e5'
    ];
    const SENDER_COLORS = [
      '#1f7a63', '#1e88e5', '#8e24aa', '#e53935', 
      '#fb8c00', '#00acc1', '#43a047', '#3949ab'
    ];

    function safeDomId(prefix, rawId) {
      if (!rawId) return prefix + 'unknown';
      return prefix + encodeURIComponent(String(rawId)).replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    function updateThemeMenuLabel(isDark) {
      const iconEl = document.getElementById('themeMenuIcon');
      const textEl = document.getElementById('themeMenuText');
      if (iconEl) iconEl.textContent = isDark ? '☀️' : '🌙';
      if (textEl) textEl.textContent = isDark ? 'Modo claro' : 'Modo oscuro';
    }

    function toggleTheme() {
      const isDark = document.body.classList.toggle('dark');
      localStorage.setItem('wa_backup_theme', isDark ? 'dark' : 'light');
      updateThemeMenuLabel(isDark);
    }

    function initTheme() {
      const saved = localStorage.getItem('wa_backup_theme');
      const isDark = saved === 'dark';
      if (isDark) {
        document.body.classList.add('dark');
      }
      updateThemeMenuLabel(isDark);
    }

    function closeAllDropdowns() {
      document.querySelectorAll('.dropdown-menu.open').forEach(el => el.classList.remove('open'));
      document.querySelectorAll('.btn-kebab.active').forEach(el => el.classList.remove('active'));
    }

    function toggleDropdown(menuId, btnEl, event) {
      if (event) event.stopPropagation();
      const menu = document.getElementById(menuId);
      if (!menu) return;
      const isOpen = menu.classList.contains('open');
      closeAllDropdowns();
      if (!isOpen) {
        menu.classList.add('open');
        if (btnEl) btnEl.classList.add('active');
      }
    }

    /* Gestión y selector de múltiples cuentas */
    async function loadAccountsList() {
      try {
        const res = await fetch('/api/accounts');
        availableAccounts = await res.json();
        renderAccountSelector();
      } catch (e) {
        console.error('Error cargando cuentas:', e);
      }
    }

    function renderAccountSelector() {
      const container = document.getElementById('accountSelectorContainer');
      const label = document.getElementById('currentAccountLabel');
      const arrow = document.getElementById('accountSelectorArrow');
      const dropdown = document.getElementById('accountDropdown');
      if (!container || !label || !dropdown) return;

      if (!availableAccounts || availableAccounts.length === 0) {
        container.style.display = 'none';
        return;
      }

      let activeAcc = availableAccounts.find(a => a.id === currentAccountId);
      if (!activeAcc || (activeAcc.id === 'default' && availableAccounts.some(a => a.id !== 'default'))) {
        const realAcc = availableAccounts.find(a => a.id !== 'default') || availableAccounts[0];
        activeAcc = realAcc;
        currentAccountId = activeAcc ? activeAcc.id : null;
        if (currentAccountId) {
          localStorage.setItem('wackup_selected_account', currentAccountId);
        }
      }

      container.style.display = 'flex';
      const displayName = activeAcc.name || formatPhoneNumber(activeAcc.phone) || activeAcc.id;
      label.textContent = displayName;
      label.title = `Cuenta activa: ${displayName} (${activeAcc.id})`;

      arrow.style.display = availableAccounts.length > 1 ? 'inline' : 'none';

      dropdown.innerHTML = '';
      availableAccounts.forEach(acc => {
        const item = document.createElement('div');
        const isActive = acc.id === currentAccountId;
        item.className = 'account-dropdown-item' + (isActive ? ' active' : '');
        item.onclick = (e) => {
          e.stopPropagation();
          selectAccount(acc.id);
        };

        const titleText = acc.name || formatPhoneNumber(acc.phone) || acc.id;
        const phoneSubtitle = (acc.phone && acc.name && acc.name !== acc.phone) ? formatPhoneNumber(acc.phone) : (acc.id !== titleText ? acc.id : '');
        const msgsSubtitle = `${(acc.total_messages || 0).toLocaleString()} msgs · ${acc.total_chats || 0} chats`;

        item.innerHTML = `
          <div class="account-item-title">
            <span>${escapeHtml(titleText)}</span>
            ${isActive ? '<span style="color: var(--accent); font-size: 11px; font-weight: 600;">✓ Activa</span>' : ''}
          </div>
          <div class="account-item-subtitle">${phoneSubtitle ? escapeHtml(phoneSubtitle) + ' · ' : ''}${msgsSubtitle}</div>
        `;
        dropdown.appendChild(item);
      });
    }

    function toggleAccountDropdown(event) {
      if (event) event.stopPropagation();
      if (!availableAccounts || availableAccounts.length <= 1) return;
      const menu = document.getElementById('accountDropdown');
      if (!menu) return;
      const isOpen = menu.classList.contains('open');
      closeAllDropdowns();
      if (!isOpen) {
        menu.classList.add('open');
      }
    }

    async function selectAccount(accountId) {
      closeAllDropdowns();
      if (currentAccountId === accountId) return;
      currentAccountId = accountId;
      localStorage.setItem('wackup_selected_account', accountId);
      selectedChatId = null;
      allChats = [];
      currentMessages = [];
      filteredMessages = [];

      document.getElementById('chat-header').style.display = 'none';
      document.getElementById('chat-filter-bar').style.display = 'none';
      document.getElementById('messages-container').style.display = 'none';
      document.getElementById('empty-state').style.display = 'flex';

      renderAccountSelector();
      showToast(`Cambiando a cuenta: ${accountId}...`);
      await updateSyncStatus();
      await loadChats();
    }

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.menu-container') && !e.target.closest('.account-selector-container')) {
        closeAllDropdowns();
      }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeAllDropdowns();
      }
    });

    function toggleChatFilterBar() {
      const bar = document.getElementById('chat-filter-bar');
      if (!bar) return;
      const isHidden = bar.style.display === 'none' || !bar.style.display;
      if (isHidden) {
        bar.style.display = 'flex';
        const menuText = document.getElementById('chatFilterMenuText');
        if (menuText) menuText.textContent = 'Ocultar barra de filtros';
      } else {
        closeChatFilterBar();
      }
    }

    function closeChatFilterBar() {
      const bar = document.getElementById('chat-filter-bar');
      if (bar) bar.style.display = 'none';
      const menuText = document.getElementById('chatFilterMenuText');
      if (menuText) menuText.textContent = 'Buscar por fecha y multimedia';
      if (activeMediaFilter !== null || document.getElementById('filterDateFrom').value || document.getElementById('filterDateTo').value) {
        activeMediaFilter = null;
        document.querySelectorAll('#chat-filter-bar .filter-pills .filter-pill').forEach((el, idx) => el.classList.toggle('active', idx === 0));
        document.getElementById('filterDateFrom').value = '';
        document.getElementById('filterDateTo').value = '';
        applyClientFilters();
      }
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 3500);
    }

    function getColorForName(name, palette = AVATAR_COLORS) {
      if (!name) return palette[0];
      let hash = 0;
      for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
      return palette[Math.abs(hash) % palette.length];
    }

    function isBinaryOrBase64(text) {
      if (!text) return false;
      const t = text.trim();
      if (t.startsWith('data:image') || t.includes('/9j/')) return true;
      if (/^[0-9a-zA-Z._+-]+@(s\\.whatsapp\\.net|lid|g\\.us|c\\.us|broadcast|newsletter)$/i.test(t)) return true;
      if (/@(s\\.whatsapp\\.net|lid|g\\.us|c\\.us|broadcast)\\b/i.test(t) && !t.includes(' ')) return true;
      if (t.length > 35 && !/\\s/.test(t) && /^[-A-Za-z0-9+/=_*&\"'()%:;$^]+$/.test(t)) return true;
      return false;
    }

    function cleanText(text) {
      if (!text) return '';
      if (isBinaryOrBase64(text)) return '';
      let t = text.replace(/\\ufffd/g, '').replace(/[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]/g, '').trim();
      // Limpiar prefijos residuales de cabeceras Protobuf (ej. "a\\n9Ya en la camita!" o "r\\nJAcá todo")
      t = t.replace(/^[A-Za-z0-9]{1,2}\\r?\\n[0-9!-/:-@\\[-`{-~](?=[A-Za-zÁÉÍÓÚáéíóúÑñ¿¡"'\\uD800-\\uDBFF])/u, '');
      t = t.replace(/^[A-Za-z0-9]{1,2}\\r?\\n[A-Za-z](?=[A-ZÁÉÍÓÚÑ¿¡][a-záéíóúñ])/u, '');
      if (isBinaryOrBase64(t)) return '';
      return t.trim();
    }

    function formatLocalDate(ts, fallbackStr) {
      if (ts) {
        const d = new Date(ts * 1000);
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
      }
      return fallbackStr ? fallbackStr.split(' ')[0] : 'Desconocida';
    }

    function formatLocalTime(ts, fallbackStr) {
      if (ts) {
        const d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      }
      return fallbackStr && fallbackStr.includes(' ') ? fallbackStr.split(' ')[1].substring(0, 5) : '';
    }

    function formatPhoneNumber(rawPhone) {
      if (!rawPhone) return '';
      const raw = String(rawPhone).split('@')[0].replace(/\\D/g, '');
      if (!raw) return '';
      if (/^549\\d{10}$/.test(raw)) {
        return `+54 9 ${raw.substring(3, 6)} ${raw.substring(6, 9)}-${raw.substring(9)}`;
      }
      if (/^54\\d{10}$/.test(raw)) {
        return `+54 ${raw.substring(2, 5)} ${raw.substring(5, 8)}-${raw.substring(8)}`;
      }
      if (/^1\\d{10}$/.test(raw)) {
        return `+1 ${raw.substring(1, 4)} ${raw.substring(4, 7)}-${raw.substring(7)}`;
      }
      if (/^\\d{7,15}$/.test(raw)) {
        return `+${raw}`;
      }
      return raw;
    }

    function formatFriendlyName(name, jid) {
      if (name && !name.endsWith('@c.us') && !name.endsWith('@g.us') && !name.endsWith('@lid') && !name.endsWith('@broadcast')) {
        return name;
      }
      const raw = (name || jid || '').split('@')[0];
      if (/^549\\d{10}$/.test(raw)) {
        return `+54 9 ${raw.substring(3, 6)} ${raw.substring(6, 9)}-${raw.substring(9)}`;
      }
      if (/^\\d{10,}$/.test(raw)) {
        return `+${raw}`;
      }
      return raw || 'Desconocido';
    }

    function formatPreview(lastMsg, lastTipo) {
      const clean = cleanText(lastMsg);
      if (clean) return clean;
      if (lastTipo === 'image') return '📷 Foto';
      if (lastTipo === 'video') return '🎬 Video';
      if (lastTipo === 'ptt') return '🎤 Mensaje de voz';
      if (lastTipo === 'audio') return '🎵 Audio';
      if (lastTipo === 'document') return '📄 Documento';
      if (lastTipo === 'sticker') return '💟 Sticker';
      if (lastTipo === 'album') return '📷 Álbum de fotos';
      if (lastTipo === 'revoked') return '🚫 Este mensaje fue eliminado';
      if (lastTipo === 'location') return '📍 Ubicación';
      if (lastTipo === 'vcard') return '👤 Contacto';
      if (lastTipo === 'poll_creation') return '📊 Encuesta';
      if (lastTipo === 'interactive' || lastTipo === 'list' || lastTipo === 'rich_response') return '💬 Mensaje interactivo';
      if (lastTipo === 'groups_v4_invite') return '👥 Invitación de grupo';
      return 'Sin mensajes';
    }

    let syncIntervalId = null;

    async function init() {
      initTheme();
      await loadAccountsList();
      await updateSyncStatus();
      await loadChats();
      // Si todavía no hay chats respaldados, consultar cada 2s para que la pantalla se actualice de inmediato al conectar WhatsApp Web
      startSyncPolling((!allChats || allChats.length === 0) ? 2000 : 4000);
    }

    function startSyncPolling(intervalMs) {
      if (syncIntervalId) clearInterval(syncIntervalId);
      syncIntervalId = setInterval(updateSyncStatus, intervalMs);
    }

    async function updateSyncStatus() {
      try {
        const res = await fetch(withAccount('/api/sync/status'));
        const data = await res.json();
        const prevLastTs = lastSyncData.last_timestamp;
        const prevTotalMsgs = lastSyncData.total_messages;
        const prevTotalChats = lastSyncData.total_chats;
        const prevValidMsgs = lastSyncData.valid_messages;
        const prevDownloadedMedia = lastSyncData.downloaded_media;
        const prevExpiredMedia = lastSyncData.expired_media;
        const hadNoChats = (!allChats || allChats.length === 0);

        lastSyncData = data;

        // Si no teníamos cuenta activa pero ahora la API nos indica una (o se crearon nuevas cuentas), refrescar selector
        if (data.account_id && (!currentAccountId || (availableAccounts.length === 0 && ((data.total_messages || 0) > 0 || (data.total_chats || 0) > 0)))) {
          await loadAccountsList();
        }

        const syncStatusEl = document.getElementById('syncStatusText');
        if (syncStatusEl) {
          syncStatusEl.textContent = `${(data.total_messages || 0).toLocaleString()} msgs`;
        }

        const dbStatsEl = document.getElementById('dbStatsText');
        if (dbStatsEl) {
          dbStatsEl.textContent = 
            `Base de datos: ${(data.total_messages || 0).toLocaleString()} mensajes | ${data.total_chats || 0} chats | ${data.downloaded_media || 0} archivos multimedia (${data.pending_media || 0} en cola)`;
        }

        if (document.getElementById('media-modal') && document.getElementById('media-modal').style.display === 'flex') {
          renderMediaStats(data);
        }

        const messagesChanged = prevLastTs !== undefined && (
          data.last_timestamp !== prevLastTs || 
          data.total_messages !== prevTotalMsgs || 
          data.valid_messages !== prevValidMsgs
        );
        const chatsChanged = prevTotalChats !== undefined && (data.total_chats !== prevTotalChats);
        const mediaChanged = prevDownloadedMedia !== undefined && (
          data.downloaded_media !== prevDownloadedMedia || 
          data.expired_media !== prevExpiredMedia
        );
        const firstDataArrived = hadNoChats && ((data.total_chats || 0) > 0 || (data.total_messages || 0) > 0);

        // Si llegaron chats o mensajes nuevos, recargar la lista de chats automáticamente
        if (messagesChanged || chatsChanged || firstDataArrived) {
          await loadChats();
          // Al pasar de 0 a tener chats, ajustar la frecuencia de refresco a 4s
          if (hadNoChats && allChats && allChats.length > 0) {
            startSyncPolling(4000);
          }
        }

        // Si el chat abierto recibió nuevos mensajes o completó descargas multimedia, refrescarlo sin perder la posición de lectura
        if ((messagesChanged || mediaChanged) && selectedChatId) {
          await refreshActiveChatSilently();
        }
      } catch (e) {
        console.error('Error al actualizar estado:', e);
        const syncStatusEl = document.getElementById('syncStatusText');
        if (syncStatusEl) syncStatusEl.textContent = 'Desconectado';
      }
    }

    async function refreshActiveChatSilently() {
      if (!selectedChatId) return;
      const container = document.getElementById('messages-container');
      // No interrumpir si el usuario está reproduciendo un audio o video
      const isPlayingMedia = Array.from(container.querySelectorAll('audio, video')).some(el => !el.paused);
      if (isPlayingMedia) return;

      const chatIdSnapshot = selectedChatId;
      try {
        const res = await fetch(withAccount(`/api/messages?chat_id=${encodeURIComponent(chatIdSnapshot)}`));
        const freshMessages = await res.json();
        if (selectedChatId !== chatIdSnapshot) return;

        let hasChanged = freshMessages.length !== currentMessages.length;
        if (!hasChanged) {
          for (let i = freshMessages.length - 1; i >= 0; i--) {
            const a = freshMessages[i];
            const b = currentMessages[i];
            if (a.msg_id !== b.msg_id || a.archivo_local !== b.archivo_local || a.estado_multimedia !== b.estado_multimedia || a.contenido !== b.contenido || a.reenviado !== b.reenviado) {
              hasChanged = true;
              break;
            }
          }
        }
        if (!hasChanged) return;

        const wasNearBottom = (container.scrollHeight - container.scrollTop - container.clientHeight) < 80;
        const prevScrollTop = container.scrollTop;
        const prevFilteredLen = filteredMessages.length;

        currentMessages = freshMessages;
        filteredMessages = computeFilteredMessages(currentMessages);
        const delta = Math.max(0, filteredMessages.length - prevFilteredLen);
        renderedCount = Math.max(PAGE_SIZE, renderedCount + delta);

        renderMessages(filteredMessages, null, !wasNearBottom);
        if (!wasNearBottom) {
          container.scrollTop = prevScrollTop;
        }
      } catch (e) {
        // Ignorar errores transitorios en segundo plano
      }
    }

    async function loadChats() {
      try {
        const prevCount = allChats ? allChats.length : 0;
        const res = await fetch(withAccount('/api/chats'));
        allChats = await res.json();
        if (document.getElementById('searchInput') && document.getElementById('searchInput').value.trim()) {
          handleSearchInput();
        } else {
          renderChats(allChats);
        }
        updateEmptyState();
        if (prevCount === 0 && allChats && allChats.length > 0) {
          showToast('¡WhatsApp Web sincronizado con éxito! Conversaciones listas.');
        }
      } catch (e) {
        console.error('Error cargando chats:', e);
      }
    }

    function updateEmptyState() {
      const emptyStateEl = document.getElementById('empty-state');
      if (!emptyStateEl || selectedChatId) return;

      if (!allChats || allChats.length === 0) {
        emptyStateEl.innerHTML = `
          <div class="onboarding-box">
            <span class="onboarding-badge">🚀 Guía de Primer Inicio</span>
            <h3>Servidor Activo y Listo</h3>
            <p class="onboarding-desc">Para comenzar a respaldar tus conversaciones de forma segura y privada, completa estos sencillos pasos:</p>
            
            <div class="onboarding-steps">
              <div class="onboarding-step">
                <div class="step-num">1</div>
                <div class="step-content">
                  <strong>Servidor local en funcionamiento</strong>
                  <span>El motor SQLite está activo y escuchando en <code>http://127.0.0.1:8080</code>.</span>
                </div>
              </div>

              <div class="onboarding-step">
                <div class="step-num">2</div>
                <div class="step-content">
                  <strong>Carga la extensión en tu navegador (Chrome / Edge / Brave)</strong>
                  <span>Abre <code>chrome://extensions</code>, activa el <em>Modo de desarrollador</em> y haz clic en <em>Cargar descomprimida</em> seleccionando esta carpeta:</span>
                  <div class="path-copy-row">
                    <input type="text" id="extPathInput" class="path-copy-input" readonly value="Cargando ruta..." />
                    <button type="button" class="btn-copy-path" onclick="copyExtensionPath()">📋 Copiar ruta</button>
                  </div>
                </div>
              </div>

              <div class="onboarding-step">
                <div class="step-num">3</div>
                <div class="step-content">
                  <strong>Abre WhatsApp Web</strong>
                  <span>Ingresa a <a href="https://web.whatsapp.com" target="_blank" style="color: var(--accent); font-weight:600; text-decoration: underline;">web.whatsapp.com</a>. Tus mensajes comenzarán a respaldarse de forma pasiva e incremental.</span>
                </div>
              </div>
            </div>

            <div class="onboarding-live-status">
              <span class="live-pulse-dot"></span>
              <span>Esperando primera sincronización desde WhatsApp Web... La pantalla se actualizará automáticamente sin recargar.</span>
            </div>
          </div>
        `;
        fetchExtensionPath();
      } else {
        emptyStateEl.innerHTML = `
          <h2>WAckup</h2>
          <p>Selecciona una conversación a la izquierda o utiliza el buscador indexado de texto completo.</p>
          <p id="dbStatsText" style="margin-top: 14px; font-size: 13px; color: var(--text-time);"></p>
        `;
        const statsEl = document.getElementById('dbStatsText');
        if (statsEl && lastSyncData && lastSyncData.total_messages !== undefined) {
          statsEl.textContent = `Base de datos: ${(lastSyncData.total_messages || 0).toLocaleString()} mensajes | ${lastSyncData.total_chats || 0} chats | ${lastSyncData.downloaded_media || 0} archivos multimedia (${lastSyncData.pending_media || 0} en cola)`;
        }
      }
    }

    let cachedExtensionPath = '';
    async function fetchExtensionPath() {
      try {
        if (!cachedExtensionPath) {
          const res = await fetch('/api/extension/info');
          const data = await res.json();
          cachedExtensionPath = data.extension_path || '';
        }
        const input = document.getElementById('extPathInput');
        if (input) input.value = cachedExtensionPath;
      } catch (e) {}
    }

    async function copyExtensionPath() {
      if (!cachedExtensionPath) {
        await fetchExtensionPath();
      }
      if (cachedExtensionPath) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(cachedExtensionPath);
        } else {
          const input = document.getElementById('extPathInput');
          if (input) {
            input.select();
            document.execCommand('copy');
          }
        }
        showToast('Ruta de la extensión copiada al portapapeles.');
      }
    }

    function renderChats(chats) {
      const list = document.getElementById('chat-list');
      list.innerHTML = '';
      if (!chats || chats.length === 0) {
        list.innerHTML = '<li style="padding: 24px 14px; color: var(--text-time); text-align: center; font-size: 13px; line-height: 1.5;">Sin conversaciones respaldadas aún.<br>Sigue la guía en pantalla.</li>';
        return;
      }
      chats.forEach(chat => {
        const li = document.createElement('li');
        li.className = 'chat-item' + (chat.chat_id === selectedChatId ? ' active' : '');
        li.id = safeDomId('chat-', chat.chat_id);
        li.onclick = () => selectChat(chat);
        
        const friendlyTitle = formatFriendlyName(chat.display_name, chat.chat_id);
        const initial = (friendlyTitle || '?')[0].toUpperCase();
        const avatarBg = getColorForName(friendlyTitle);

        let timeStr = '';
        if (chat.last_ts) {
          const d = new Date(chat.last_ts * 1000);
          timeStr = d.toLocaleDateString() === new Date().toLocaleDateString() 
            ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : d.toLocaleDateString([], { day: '2-digit', month: '2-digit' });
        }

        const previewText = formatPreview(chat.last_msg, chat.last_tipo);

        li.innerHTML = `
          <div class="chat-avatar" style="background-color: ${avatarBg};">${initial}</div>
          <div class="chat-info">
            <div class="chat-header-row">
              <span class="chat-name">${escapeHtml(friendlyTitle)}</span>
              <span class="chat-time">${timeStr}</span>
            </div>
            <div class="chat-preview">${escapeHtml(previewText)}</div>
          </div>
        `;
        list.appendChild(li);
      });
    }

    function setSearchMode(mode) {
      currentMode = mode;
      document.getElementById('tabChats').classList.toggle('active', mode === 'chats');
      document.getElementById('tabGlobal').classList.toggle('active', mode === 'global');
      document.getElementById('searchInput').placeholder = mode === 'chats' ? 'Buscar un chat...' : 'Buscar en todos los mensajes (FTS5)...';
      
      if (mode === 'chats') {
        document.getElementById('chat-list').style.display = 'block';
        document.getElementById('search-results-list').style.display = 'none';
        handleSearchInput();
      } else {
        document.getElementById('chat-list').style.display = 'none';
        document.getElementById('search-results-list').style.display = 'block';
        handleSearchInput();
      }
    }

    function clearSearchInput() {
      const input = document.getElementById('searchInput');
      input.value = '';
      handleSearchInput();
      input.focus();
    }

    function handleSearchInput() {
      const input = document.getElementById('searchInput');
      const query = input.value.trim();
      const clearBtn = document.getElementById('searchClearBtn');
      if (clearBtn) {
        clearBtn.style.display = input.value.length > 0 ? 'flex' : 'none';
      }
      if (currentMode === 'chats') {
        const q = query.toLowerCase();
        const filtered = allChats.filter(c => {
          const name = (c.display_name || '').toLowerCase();
          const id = (c.chat_id || '').toLowerCase();
          const last = (c.last_msg || '').toLowerCase();
          const phone = (c.phone_number || '').toLowerCase();
          const phoneFmt = formatPhoneNumber(c.phone_number).toLowerCase();
          return name.includes(q) || id.includes(q) || last.includes(q) || phone.includes(q) || phoneFmt.includes(q);
        });
        renderChats(filtered);
      } else {
        clearTimeout(searchDebounceTimer);
        if (!query) {
          document.getElementById('search-results-list').innerHTML = '<li style="padding: 24px; color: var(--text-time); text-align: center;">Escribe una palabra o frase para buscar...</li>';
          return;
        }
        searchDebounceTimer = setTimeout(() => performGlobalSearch(query), 300);
      }
    }

    async function performGlobalSearch(query) {
      const list = document.getElementById('search-results-list');
      list.innerHTML = '<li style="padding: 24px; color: var(--text-time); text-align: center;">Buscando en base de datos...</li>';
      try {
        const res = await fetch(withAccount(`/api/search?q=${encodeURIComponent(query)}`));
        const results = await res.json();
        list.innerHTML = '';
        if (results.length === 0) {
          list.innerHTML = '<li style="padding: 24px; color: var(--text-time); text-align: center;">No se encontraron mensajes coincidentes.</li>';
          return;
        }
        results.forEach(item => {
          const li = document.createElement('li');
          li.className = 'search-item';
          const title = formatFriendlyName(item.chat_nombre, item.chat_id);
          li.onclick = () => {
            const chat = allChats.find(c => c.chat_id === item.chat_id) || { chat_id: item.chat_id, display_name: title };
            selectChat(chat, item.msg_id);
          };
          li.innerHTML = `
            <div class="search-chat-title">${escapeHtml(title)}</div>
            <div class="search-snippet">${item.snippet || escapeHtml(cleanText(item.contenido || ''))}</div>
            <div class="search-date">${escapeHtml(item.fecha_utc || '')}</div>
          `;
          list.appendChild(li);
        });
      } catch (e) {
        list.innerHTML = '<li style="padding: 24px; color: #d32f2f; text-align: center;">Error al realizar la búsqueda.</li>';
      }
    }

    async function selectChat(chat, scrollToMsgId = null) {
      selectedChatId = chat.chat_id;
      activeMediaFilter = null;
      document.querySelectorAll('#chat-filter-bar .filter-pills .filter-pill').forEach((el, idx) => el.classList.toggle('active', idx === 0));
      document.getElementById('filterDateFrom').value = '';
      document.getElementById('filterDateTo').value = '';
      const menuText = document.getElementById('chatFilterMenuText');
      if (menuText) menuText.textContent = 'Buscar por fecha y multimedia';

      document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
      const activeEl = document.getElementById(safeDomId('chat-', chat.chat_id));
      if (activeEl) activeEl.classList.add('active');

      document.getElementById('empty-state').style.display = 'none';
      document.getElementById('chat-header').style.display = 'flex';
      document.getElementById('chat-filter-bar').style.display = 'none';
      document.getElementById('messages-container').style.display = 'flex';

      const friendlyTitle = formatFriendlyName(chat.display_name, chat.chat_id);
      document.getElementById('headerTitle').textContent = friendlyTitle;
      
      const isGroup = chat.chat_id.endsWith('@g.us');
      const isNewsletter = chat.chat_id.endsWith('@newsletter');
      const knownChat = allChats.find(c => c.chat_id === chat.chat_id) || chat;
      const rawPhone = (!isGroup && !isNewsletter)
        ? (knownChat.phone_number || ((chat.chat_id.endsWith('@c.us') || chat.chat_id.endsWith('@s.whatsapp.net')) ? chat.chat_id.split('@')[0] : null))
        : null;

      const headerSubEl = document.getElementById('headerSub');
      if (isGroup) {
        headerSubEl.textContent = 'Grupo de WhatsApp';
      } else if (isNewsletter) {
        headerSubEl.textContent = 'Canal de WhatsApp';
      } else if (rawPhone) {
        const formattedPhone = formatPhoneNumber(rawPhone);
        headerSubEl.innerHTML = `
          <span class="header-phone-row">
            <span class="header-phone-text">${escapeHtml(formattedPhone)}</span>
            <button type="button" class="btn-copy-phone" onclick="copyChatPhone('${escapeHtml(formattedPhone)}', this)" title="Copiar número de teléfono">
              <svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
              <span class="copy-label">Copiar</span>
            </button>
          </span>
        `;
      } else {
        headerSubEl.textContent = 'Conversación directa';
      }
      
      const avatarEl = document.getElementById('headerAvatar');
      avatarEl.textContent = (friendlyTitle || '?')[0].toUpperCase();
      avatarEl.style.backgroundColor = getColorForName(friendlyTitle);

      const container = document.getElementById('messages-container');
      container.innerHTML = '<p style="text-align: center; color: var(--text-muted); margin-top: 24px;">Cargando mensajes...</p>';

      const res = await fetch(withAccount(`/api/messages?chat_id=${encodeURIComponent(chat.chat_id)}`));
      currentMessages = await res.json();
      filteredMessages = currentMessages;

      if (scrollToMsgId) {
        const targetIdx = filteredMessages.findIndex(m => m.msg_id === scrollToMsgId);
        if (targetIdx !== -1) {
          renderedCount = Math.max(PAGE_SIZE, filteredMessages.length - targetIdx + 50);
        } else {
          renderedCount = PAGE_SIZE;
        }
      } else {
        renderedCount = PAGE_SIZE;
      }

      renderMessages(filteredMessages, scrollToMsgId);
    }

    function loadOlderMessages() {
      const container = document.getElementById('messages-container');
      const prevScrollHeight = container.scrollHeight;
      renderedCount += PAGE_SIZE;
      renderMessages(filteredMessages, null, true);
      container.scrollTop = container.scrollHeight - prevScrollHeight;
    }

    function highlightBubble(el) {
      if (!el) return;
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.style.transition = 'outline 0.2s ease, box-shadow 0.2s ease';
      el.style.outline = '3px solid var(--accent)';
      el.style.boxShadow = '0 0 16px rgba(0, 168, 132, 0.6)';
      setTimeout(() => {
        el.style.outline = 'none';
        el.style.boxShadow = 'none';
      }, 2500);
    }

    function findMessageById(rawId) {
      if (!rawId) return null;
      const cleanRaw = String(rawId).trim();
      // 1. Coincidencia exacta o por subcadena/stanzaId en filteredMessages
      let found = filteredMessages.find(m => m.msg_id === cleanRaw);
      if (found) return found;

      found = filteredMessages.find(m => m.msg_id && (m.msg_id.includes(cleanRaw) || cleanRaw.includes(m.msg_id)));
      if (found) return found;

      // 2. Si está en currentMessages pero oculto por filtros de fecha/medios
      found = currentMessages.find(m => m.msg_id === cleanRaw || (m.msg_id && (m.msg_id.includes(cleanRaw) || cleanRaw.includes(m.msg_id))));
      if (found) {
        closeChatFilterBar();
        return found;
      }
      return null;
    }

    function scrollToMessage(rawId) {
      if (!rawId) return;
      const targetMsg = findMessageById(rawId);
      if (!targetMsg) {
        const directEl = document.getElementById(safeDomId('msg-', rawId));
        if (directEl) {
          highlightBubble(directEl);
        } else {
          showToast('El mensaje citado no se encuentra en el historial descargado.');
        }
        return;
      }

      const actualId = targetMsg.msg_id;
      const idx = filteredMessages.findIndex(m => m.msg_id === actualId);

      if (idx !== -1) {
        const startIdx = Math.max(0, filteredMessages.length - renderedCount);
        if (idx < startIdx) {
          // El mensaje está más arriba en el historial paginado: cargar los mensajes necesarios
          renderedCount = Math.max(renderedCount, filteredMessages.length - idx + 50);
          renderMessages(filteredMessages, actualId);
          return;
        }
      }

      const targetEl = document.getElementById(safeDomId('msg-', actualId));
      if (targetEl) {
        highlightBubble(targetEl);
      } else if (idx !== -1) {
        renderedCount = Math.max(renderedCount, filteredMessages.length - idx + 50);
        renderMessages(filteredMessages, actualId);
      }
    }

    function renderMessages(messages, scrollToMsgId = null, preserveScroll = false) {
      const container = document.getElementById('messages-container');
      container.innerHTML = '';
      let lastDate = null;
      const isGroup = selectedChatId && selectedChatId.endsWith('@g.us');

      if (messages.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-time); margin-top: 30px;">No hay mensajes coincidentes con los filtros.</p>';
        return;
      }

      const startIdx = Math.max(0, messages.length - renderedCount);
      const visibleMessages = messages.slice(startIdx);

      if (startIdx > 0) {
        const loadMoreBtn = document.createElement('button');
        loadMoreBtn.className = 'btn-load-more';
        loadMoreBtn.textContent = `⬆️ Cargar mensajes anteriores (${startIdx.toLocaleString()} restantes)`;
        loadMoreBtn.onclick = loadOlderMessages;
        container.appendChild(loadMoreBtn);
      }

      const fragment = document.createDocumentFragment();

      visibleMessages.forEach(m => {
        if (['e2e_notification', 'notification_template', 'gp2', 'protocol', 'ciphertext'].includes(m.tipo)) {
          return;
        }
        const cleanBody = cleanText(m.contenido);
        const hasMedia = Boolean(m.archivo_local || m.estado_multimedia === 'expirado' || (m.thumbnail && m.thumbnail.startsWith('data:image')));
        if (!cleanBody && !hasMedia && !m.quoted_body) {
          // Si es un mensaje cuyo contenido era un JID o basura cifrada aún pendiente de re-sincronizar
          return;
        }

        const msgDate = formatLocalDate(m.timestamp, m.fecha_utc);
        if (msgDate !== lastDate) {
          const dateDiv = document.createElement('div');
          dateDiv.className = 'date-badge';
          dateDiv.textContent = msgDate;
          fragment.appendChild(dateDiv);
          lastDate = msgDate;
        }

        const isMine = Boolean(m.es_mio) || (m.msg_id && m.msg_id.startsWith('true_'));

        const bubble = document.createElement('div');
        bubble.className = `msg-bubble ${isMine ? 'msg-outgoing' : 'msg-incoming'}`;
        if (m.msg_id) bubble.id = safeDomId('msg-', m.msg_id);

        let senderHtml = '';
        if (isGroup && !isMine) {
          const senderDisplayName = formatFriendlyName(m.remitente_nombre, m.remitente_jid);
          const senderColor = getColorForName(senderDisplayName, SENDER_COLORS);
          senderHtml = `<div class="msg-sender" style="color: ${senderColor};">${escapeHtml(senderDisplayName)}</div>`;
        }

        let forwardedHtml = '';
        if (m.reenviado && m.reenviado > 0) {
          const isMany = m.reenviado >= 5;
          const fwdLabel = isMany ? 'Reenviado muchas veces' : 'Reenviado';
          const fwdIcon = isMany
            ? `<svg viewBox="0 0 20 16"><path d="M8.5 3.5L13 8l-4.5 4.5V9.8c-3.2 0-5.4 1.1-6.8 3.7 0.7-3.6 2.6-6.2 6.8-6.9V3.5zm5.5 0L18.5 8 14 12.5v-2l2.5-2.5L14 5.5v-2z"/></svg>`
            : `<svg viewBox="0 0 16 16"><path d="M9.5 3.5L14 8l-4.5 4.5V9.8c-3.5 0-6 1.1-7.5 3.7 0.8-3.6 2.9-6.2 7.5-6.9V3.5z"/></svg>`;
          forwardedHtml = `<div class="msg-forwarded">${fwdIcon}<span>${fwdLabel}</span></div>`;
        }

        let quoteHtml = '';
        if (m.quoted_body || m.quoted_msg_id) {
          const qAuthor = m.quoted_author ? formatFriendlyName(m.quoted_author, m.quoted_author) : 'Mensaje citado';
          const qText = cleanText(m.quoted_body) || '📎 Archivo / Mensaje citado';
          const clickAttr = m.quoted_msg_id ? `onclick="scrollToMessage(this.dataset.quotedId)" data-quoted-id="${escapeHtml(m.quoted_msg_id)}" title="Ir al mensaje citado"` : '';
          quoteHtml = `
            <div class="msg-quote" ${clickAttr}>
              <div class="msg-quote-author">${escapeHtml(qAuthor)}</div>
              <div class="msg-quote-text">${escapeHtml(qText)}</div>
            </div>`;
        }

        let mediaHtml = '';
        if (m.archivo_local) {
          if (m.tipo === 'image' || m.tipo === 'sticker' || (m.mimetype && m.mimetype.startsWith('image/'))) {
            mediaHtml = `<div class="media-container"><img class="media-img" src="/${m.archivo_local}" onclick="openModal(this.src)" loading="lazy"></div>`;
          } else if (m.tipo === 'audio' || m.tipo === 'ptt' || (m.mimetype && m.mimetype.startsWith('audio/'))) {
            mediaHtml = `
              <div class="media-container audio-wrapper">
                <audio class="media-audio" controls preload="metadata" src="/${m.archivo_local}" onended="playNextAudio(this)"></audio>
                <button class="btn-speed" onclick="toggleAudioSpeed(this)">1x</button>
              </div>`;
          } else if (m.tipo === 'video' || (m.mimetype && m.mimetype.startsWith('video/'))) {
            mediaHtml = `<div class="media-container"><video class="media-video" controls preload="metadata" src="/${m.archivo_local}"></video></div>`;
          } else {
            const fileName = m.archivo_local.split('/').pop();
            mediaHtml = `<div class="media-container"><a class="media-doc" href="/${m.archivo_local}" download><span class="media-doc-icon">📄</span><span>${escapeHtml(fileName)}</span></a></div>`;
          }
        } else if (m.estado_multimedia === 'expirado') {
          mediaHtml = `<div class="media-expired"><span>⚠️</span><span>[Archivo multimedia expirado en servidores de WhatsApp]</span></div>`;
        } else if (m.thumbnail && m.thumbnail.startsWith('data:image')) {
          mediaHtml = `<div class="media-container"><img class="media-img" src="${m.thumbnail}" onclick="openModal(this.src)"></div>`;
        }

        const timeStr = formatLocalTime(m.timestamp, m.fecha_utc);
        const checkHtml = isMine ? '<span class="msg-check">✓✓</span>' : '';

        bubble.innerHTML = `
          ${senderHtml}
          ${forwardedHtml}
          ${quoteHtml}
          ${mediaHtml}
          ${cleanBody ? `<div class="msg-text">${escapeHtml(cleanBody)}</div>` : ''}
          <div class="msg-footer">
            <span>${timeStr}</span>
            ${checkHtml}
          </div>
        `;
        fragment.appendChild(bubble);
      });

      container.appendChild(fragment);

      if (scrollToMsgId) {
        const target = document.getElementById(safeDomId('msg-', scrollToMsgId));
        if (target) {
          highlightBubble(target);
          return;
        }
      }
      if (!preserveScroll) {
        container.scrollTop = container.scrollHeight;
      }
    }

    // Filtros multimedia en el chat
    function setMediaFilter(type, el) {
      activeMediaFilter = type;
      document.querySelectorAll('#chat-filter-bar .filter-pills .filter-pill').forEach(pill => pill.classList.remove('active'));
      el.classList.add('active');
      applyClientFilters();
    }

    function applyDateFilter() {
      applyClientFilters();
    }

    function computeFilteredMessages(messages) {
      const fromVal = document.getElementById('filterDateFrom').value;
      const toVal = document.getElementById('filterDateTo').value;

      let filtered = messages;

      if (activeMediaFilter) {
        if (activeMediaFilter === 'audio') {
          filtered = filtered.filter(m => m.tipo === 'audio' || m.tipo === 'ptt' || (m.mimetype && m.mimetype.startsWith('audio/')));
        } else if (activeMediaFilter === 'image') {
          filtered = filtered.filter(m => m.tipo === 'image' || (m.mimetype && m.mimetype.startsWith('image/')));
        } else if (activeMediaFilter === 'video') {
          filtered = filtered.filter(m => m.tipo === 'video' || (m.mimetype && m.mimetype.startsWith('video/')));
        } else if (activeMediaFilter === 'document') {
          filtered = filtered.filter(m => m.tipo === 'document' || (m.mimetype && !m.mimetype.startsWith('image/') && !m.mimetype.startsWith('audio/') && !m.mimetype.startsWith('video/')));
        }
      }

      if (fromVal) {
        const fromTs = new Date(fromVal + 'T00:00:00').getTime() / 1000;
        filtered = filtered.filter(m => (m.timestamp || 0) >= fromTs);
      }
      if (toVal) {
        const toTs = new Date(toVal + 'T23:59:59').getTime() / 1000;
        filtered = filtered.filter(m => (m.timestamp || 0) <= toTs);
      }
      return filtered;
    }

    function applyClientFilters() {
      filteredMessages = computeFilteredMessages(currentMessages);
      renderedCount = PAGE_SIZE;
      renderMessages(filteredMessages);
    }

    // Control de velocidad y reproducción continua de audios
    function toggleAudioSpeed(btn) {
      const wrapper = btn.closest('.audio-wrapper');
      const audio = wrapper.querySelector('audio');
      if (!audio) return;

      const speeds = [1, 1.5, 2];
      const current = audio.playbackRate;
      let nextIdx = (speeds.indexOf(current) + 1) % speeds.length;
      audio.playbackRate = speeds[nextIdx];
      btn.textContent = speeds[nextIdx] + 'x';
    }

    function playNextAudio(currentAudio) {
      const allAudios = Array.from(document.querySelectorAll('.media-audio'));
      const idx = allAudios.indexOf(currentAudio);
      if (idx !== -1 && idx + 1 < allAudios.length) {
        const next = allAudios[idx + 1];
        next.currentTime = 0;
        next.play();
        next.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }

    // Exportación de Chat a HTML y Contactos a CSV
    function exportCurrentChat(embedMedia = false) {
      if (!selectedChatId) return;
      const embedParam = embedMedia ? '&embed=1' : '';
      window.open(withAccount(`/api/export/html?chat_id=${encodeURIComponent(selectedChatId)}${embedParam}`), '_blank');
    }

    function exportContactsCsv() {
      showToast('Descargando contactos en formato CSV...');
      window.location.href = withAccount('/api/export/contacts');
    }

    function openSyncLogs() {
      fetch('/api/logs/open', { method: 'POST' }).then(() => {
        showToast('Ventana de logs abierta.');
      }).catch(() => {
        window.open('/logs', '_blank');
      });
    }

    async function copyChatPhone(phoneText, btnEl) {
      if (!phoneText) return;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(phoneText);
        } else {
          const ta = document.createElement('textarea');
          ta.value = phoneText;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
        }
        if (btnEl) {
          const labelEl = btnEl.querySelector('.copy-label');
          btnEl.classList.add('copied');
          if (labelEl) labelEl.textContent = '✓ Copiado';
          setTimeout(() => {
            btnEl.classList.remove('copied');
            if (labelEl) labelEl.textContent = 'Copiar';
          }, 2000);
        }
        showToast(`📋 Número copiado: ${phoneText}`);
      } catch (e) {
        showToast('Error al copiar el número al portapapeles');
      }
    }

    // Gestión de Multimedia
    async function openMediaModal() {
      const modal = document.getElementById('media-modal');
      modal.style.display = 'flex';
      await updateSyncStatus();
      renderMediaStats(lastSyncData);
    }

    function closeMediaModal() {
      document.getElementById('media-modal').style.display = 'none';
    }

    function renderMediaStats(data) {
      document.getElementById('statMediaDownloaded').textContent = (data.downloaded_media || 0).toLocaleString();
      document.getElementById('statMediaPending').textContent = (data.pending_media || 0).toLocaleString();
      document.getElementById('statMediaFailed').textContent = (data.failed_media || 0).toLocaleString();
      document.getElementById('statMediaExpired').textContent = (data.expired_media || 0).toLocaleString();
    }

    async function retryFailedMedia() {
      showToast('Reencolando descargas multimedia fallidas...');
      try {
        const res = await fetch(withAccount('/api/media/retry'), { method: 'POST' });
        const data = await res.json();
        showToast(`Se reencolaron ${data.reset_count || 0} archivos para descarga.`);
        await updateSyncStatus();
      } catch (e) {
        showToast('Error al reintentar descargas multimedia');
      }
    }

    // Gestión de Backups
    async function openBackupModal() {
      const modal = document.getElementById('backup-modal');
      modal.style.display = 'flex';
      await loadBackupList();
    }

    function closeBackupModal() {
      document.getElementById('backup-modal').style.display = 'none';
    }

    async function loadBackupList() {
      const container = document.getElementById('backupListContainer');
      container.innerHTML = '<p style="padding: 14px; color: var(--text-time); text-align: center;">Cargando respaldos...</p>';
      try {
        const res = await fetch(withAccount('/api/backup/list'));
        const backups = await res.json();
        if (backups.length === 0) {
          container.innerHTML = '<p style="padding: 14px; color: var(--text-time); text-align: center;">No hay copias de seguridad manuales aún.</p>';
          return;
        }
        container.innerHTML = '';
        backups.forEach(b => {
          const div = document.createElement('div');
          div.className = 'backup-item';
          const downloadUrl = withAccount(`/api/backup/download?file=${encodeURIComponent(b.filename)}`);
          div.innerHTML = `
            <div>
              <strong>${escapeHtml(b.filename)}</strong>
              <div style="color: var(--text-time); font-size: 11px;">${b.created_at} · ${b.size_mb} MB</div>
            </div>
            <a href="${downloadUrl}" class="btn-chat-action" style="text-decoration: none;">Descargar</a>
          `;
          container.appendChild(div);
        });
      } catch (e) {
        container.innerHTML = '<p style="padding: 14px; color: #d32f2f; text-align: center;">Error al cargar lista de backups.</p>';
      }
    }

    async function triggerManualBackup() {
      showToast('Creando copia de seguridad consistente de SQLite...');
      try {
        const res = await fetch(withAccount('/api/backup/create'), { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast(`¡Backup creado con éxito! (${data.backup.size_mb} MB)`);
          await loadBackupList();
        } else {
          showToast('Error al crear backup: ' + (data.error || 'Desconocido'));
        }
      } catch (e) {
        showToast('Error de conexión al generar backup');
      }
    }

    function openModal(src) {
      const modal = document.getElementById('image-modal');
      document.getElementById('modalImg').src = src;
      modal.style.display = 'flex';
    }

    function escapeHtml(text) {
      if (!text) return '';
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    init();
  </script>
</body>
</html>
"""

def _file_to_data_uri(rel_path, default_mime="application/octet-stream", max_size_mb=15):
    """Convierte un archivo local en un Data URI base64 si existe y no supera el límite."""
    try:
        safe_rel = rel_path.lstrip("/").replace("/", os.sep)
        full_path = os.path.join(BASE_DIR, safe_rel)
        if not os.path.isfile(full_path):
            alt_path = os.path.join(DATA_DIR, safe_rel)
            if os.path.isfile(alt_path):
                full_path = alt_path
            else:
                return None
        if os.path.getsize(full_path) > max_size_mb * 1024 * 1024:
            return None
        mime, _ = mimetypes.guess_type(full_path)
        mime = mime or default_mime
        with open(full_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None

def generate_chat_html_export(chat_id, embed_media=False, db_path=None):
    """Genera un archivo HTML autocontenido para imprimir o archivar."""
    conn = db_manager.get_connection(db_path or config.get_default_db())
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(c.nombre, ct.nombre, ct.push_name, m.chat_id) AS display_name
        FROM (SELECT DISTINCT chat_id FROM mensajes WHERE chat_id = ?) m
        LEFT JOIN chats c ON m.chat_id = c.chat_id
        LEFT JOIN contactos ct ON m.chat_id = ct.contact_id
    """, (chat_id,))
    row = cur.fetchone()
    display_name = row["display_name"] if row else chat_id

    contact_map = db_manager.build_contact_map(cur)
    cur.execute("""
        SELECT 
            m.msg_id, m.es_mio, m.fecha_utc, m.timestamp, m.tipo, m.contenido,
            m.archivo_local, m.mimetype, m.thumbnail,
            m.quoted_msg_id, m.quoted_author, m.quoted_body, m.reenviado,
            COALESCE(ct.nombre, ct.push_name, m.remitente) AS remitente_nombre,
            m.remitente AS remitente_jid
        FROM mensajes m
        LEFT JOIN contactos ct ON m.remitente = ct.contact_id
        WHERE m.chat_id = ?
        ORDER BY m.timestamp ASC
    """, (chat_id,))
    messages = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Mapa para resolver citas que no traían el texto incrustado
    msg_map = {}
    for m in messages:
        mid = m.get("msg_id") or ""
        msg_map[mid] = m
        for p in mid.split("_"):
            if len(p) >= 12:
                msg_map[p] = m

    is_group = chat_id.endswith("@g.us")
    
    html_parts = [f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Respaldo: {html.escape(display_name)}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f0f2f5; margin: 0; padding: 20px; color: #111b21; }}
    .container {{ max-width: 800px; margin: 0 auto; background: #efeae2; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
    .header {{ background: #00a884; color: #ffffff; padding: 18px 24px; }}
    .header h1 {{ margin: 0; font-size: 20px; }}
    .header p {{ margin: 4px 0 0 0; font-size: 13px; opacity: 0.9; }}
    .messages {{ padding: 20px; display: flex; flex-direction: column; gap: 6px; }}
    .date-badge {{ align-self: center; background: #ffffff; color: #54656f; font-size: 11px; padding: 4px 12px; border-radius: 6px; margin: 10px 0; }}
    .msg {{ max-width: 70%; padding: 7px 10px; border-radius: 8px; font-size: 14px; line-height: 19px; box-shadow: 0 1px 0.5px rgba(0,0,0,0.13); word-break: break-word; }}
    .incoming {{ align-self: flex-start; background: #ffffff; }}
    .outgoing {{ align-self: flex-end; background: #d9fdd3; }}
    .sender {{ font-size: 12px; font-weight: bold; color: #1f7a63; margin-bottom: 2px; }}
    .forwarded {{ font-size: 12px; font-style: italic; color: #667781; margin-bottom: 2px; }}
    .quote {{ background: rgba(0,0,0,0.05); border-left: 3px solid #00a884; padding: 3px 7px; border-radius: 4px; font-size: 12px; color: #54656f; margin-bottom: 4px; }}
    .time {{ font-size: 10.5px; color: #667781; text-align: right; margin-top: 4px; }}
    .media-img {{ max-width: 300px; border-radius: 6px; display: block; margin-bottom: 4px; }}
    @media print {{ body {{ background: #fff; padding: 0; }} .container {{ box-shadow: none; border-radius: 0; }} }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>{html.escape(display_name)}</h1>
      <p>{len(messages)} mensajes respaldados · Exportado el {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
    </div>
    <div class="messages">
"""]

    last_date = None
    for m in messages:
        msg_date = m["fecha_utc"].split(" ")[0] if m.get("fecha_utc") else "Fecha desconocida"
        if msg_date != last_date:
            html_parts.append(f'<div class="date-badge">{msg_date}</div>')
            last_date = msg_date

        is_mine = bool(m.get("es_mio")) or (m.get("msg_id", "").startswith("true_"))
        cls = "outgoing" if is_mine else "incoming"
        sender_html = ""
        if is_group and not is_mine:
            name = m["remitente_nombre"] or m["remitente_jid"] or "Desconocido"
            sender_html = f'<div class="sender">{html.escape(name)}</div>'

        forwarded_html = ""
        if m.get("reenviado") and m["reenviado"] > 0:
            fwd_text = "↪↪ Reenviado muchas veces" if m["reenviado"] >= 5 else "↪ Reenviado"
            forwarded_html = f'<div class="forwarded">{fwd_text}</div>'

        quote_html = ""
        q_body = m.get("quoted_body")
        if not q_body and m.get("quoted_msg_id"):
            qid = m["quoted_msg_id"]
            if qid in msg_map and msg_map[qid].get("contenido"):
                q_body = msg_map[qid]["contenido"]
        if q_body:
            q_body_resolved = db_manager.resolve_mentions_in_text(q_body, contact_map)
            quote_html = f'<div class="quote">{html.escape(q_body_resolved)}</div>'

        media_html = ""
        if m.get("archivo_local"):
            rel_url = f'/{m["archivo_local"]}'
            src_uri = (_file_to_data_uri(m["archivo_local"], m.get("mimetype") or "application/octet-stream") if embed_media else None) or rel_url
            if m.get("tipo") in ("image", "sticker"):
                media_html = f'<img class="media-img" src="{src_uri}">'
            elif m.get("tipo") in ("audio", "ptt"):
                media_html = f'<audio controls src="{src_uri}"></audio>'
            else:
                media_html = f'<div>📄 <a href="{rel_url}">{html.escape(m["archivo_local"].split("/")[-1])}</a></div>'
        elif m.get("thumbnail") and str(m.get("thumbnail")).startswith("data:image"):
            media_html = f'<img class="media-img" src="{m["thumbnail"]}">'

        time_str = m["fecha_utc"].split(" ")[1][:5] if m.get("fecha_utc") else ""
        raw_c = m.get("contenido") or ""
        content_clean = "" if db_manager.is_binary_or_base64_content(m.get("tipo"), raw_c) else db_manager.resolve_mentions_in_text(raw_c.replace("\ufffd", "").strip(), contact_map)

        html_parts.append(f"""
        <div class="msg {cls}">
          {sender_html}
          {forwarded_html}
          {quote_html}
          {media_html}
          {f'<div>{html.escape(content_clean)}</div>' if content_clean else ''}
          <div class="time">{time_str}</div>
        </div>
        """)

    html_parts.append("""
    </div>
  </div>
</body>
</html>
""")
    return "".join(html_parts)

_sync_lock = threading.Lock()
_ui_lock = threading.Lock()
_sync_batch_count = 0
_total_messages_synced = 0
_last_sync_status_text = "Esperando datos de sincronización..."
_last_update_status_text = "Presiona [U] para comprobar"
_update_in_progress = False
_server_url = None

def get_disk_extension_version():
    """Lee la versión de la extensión declarada en extension/manifest.json."""
    try:
        manifest_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extension", "manifest.json")
        if os.path.isfile(manifest_file):
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("version", "1.0.0")
    except Exception:
        pass
    return "1.0.0"

def run_git_cmd(args, timeout=15):
    """Ejecuta un comando de Git garantizando safe.directory y devolviendo CompletedProcess."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = ["git", "-c", "safe.directory=*"] + args
    try:
        res = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0 and "dubious ownership" in (res.stderr or ""):
            safe_path = base_dir.replace("\\", "/")
            subprocess.run(["git", "config", "--global", "--add", "safe.directory", safe_path], capture_output=True)
            res = subprocess.run(cmd, cwd=base_dir, capture_output=True, text=True, timeout=timeout)
        return res
    except Exception as e:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))

def render_dashboard(url=None):
    """Dibuja el panel de control completo sin scroll vertical en la consola."""
    global _server_url
    with _ui_lock:
        if url:
            _server_url = url
        current_url = _server_url or f"http://127.0.0.1:{PORT}"
        os.system("cls" if os.name == "nt" else "clear")
        lines = [
            "========================================================================",
            "                   WACKUP - PANEL DE CONTROL & VISOR                    ",
            "========================================================================",
            f"  Dirección local:       {current_url}",
            "",
            "  Atajos en esta consola:",
            "    [V]        Abrir el visor en el navegador",
            "    [L]        Abrir ventana de logs de sincronización en vivo",
            "    [U]        Buscar y aplicar actualizaciones de GitHub",
            "    [Ctrl + C] Detener el servidor",
            "",
            r"  Archivo de logs:       data\logs\sync.log (o ejecuta ver_logs.bat)",
            "========================================================================",
            f"  Actualizaciones:       {_last_update_status_text}",
            "========================================================================",
            f"  Estado: {_last_sync_status_text}"
        ]
        # Dejar el cursor al final de la última línea con \r para actualizaciones directas
        sys.stdout.write("\n".join(lines) + "\r")
        sys.stdout.flush()

def check_and_apply_update(url):
    """Verifica si hay actualizaciones en GitHub sin provocar scroll en el panel."""
    global _last_update_status_text, _update_in_progress
    if _update_in_progress:
        return
    _update_in_progress = True

    base_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(os.path.join(base_dir, ".git")):
        if shutil.which("git"):
            _last_update_status_text = "Vinculando carpeta con repositorio de GitHub..."
            render_dashboard(url)
            repo_url = "https://github.com/dbonotti/wackup.git"
            run_git_cmd(["init", "-b", "main"])
            run_git_cmd(["remote", "add", "origin", repo_url])
            res_init = run_git_cmd(["fetch", "origin", "main", "--quiet"], timeout=15)
            if res_init.returncode == 0:
                run_git_cmd(["reset", "--hard", "origin/main"])
                _last_update_status_text = "[OK] Carpeta vinculada y actualizada con éxito desde GitHub."
            else:
                _last_update_status_text = "[Aviso] Carpeta ZIP. Conecta a internet para vincular."
                _update_in_progress = False
                render_dashboard(url)
                return
        else:
            _last_update_status_text = "[Aviso] Carpeta ZIP. Instala Git (git-scm.com) para actualizar en 1 clic."
            _update_in_progress = False
            render_dashboard(url)
            return

    now_str = datetime.now().strftime("%H:%M:%S")
    _last_update_status_text = f"Consultando servidor de GitHub... [{now_str}]"
    render_dashboard(url)

    res = run_git_cmd(["fetch", "origin", "main", "--quiet"], timeout=12)
    now_str = datetime.now().strftime("%H:%M:%S")
    if res.returncode != 0:
        err = (res.stderr or "").strip().lower()
        if "could not resolve host" in err or "failed to connect" in err or "timed out" in err:
            err_desc = "Sin conexión a GitHub (verifica tu internet)"
        elif "not appear to be a git repository" in err or "not found" in err:
            err_desc = "Repositorio remoto no encontrado"
        elif "permission denied" in err or "authentication" in err:
            err_desc = "Error de autenticación con GitHub"
        else:
            first_line = (res.stderr or "").strip().split("\n")[0]
            err_desc = first_line[:50] if first_line else "Error consultando GitHub"

        append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Update Error] {res.stderr or res.stdout}")
        _last_update_status_text = f"[Error] {err_desc} ({now_str})"
        _update_in_progress = False
        render_dashboard(url)
        return

    count_res = run_git_cmd(["rev-list", "HEAD..origin/main", "--count"], timeout=10)
    count = int(count_res.stdout.strip() or "0")
    if count == 0:
        _last_update_status_text = f"[✓] ¡Sistema al día con GitHub! (comprobado {now_str})"
        _update_in_progress = False
        render_dashboard(url)
        return

    _last_update_status_text = f"[!] Hay {count} actualización(es) disponible(s)"

    # Mostrar detalles de actualización en vista limpia
    os.system("cls" if os.name == "nt" else "clear")
    print("========================================================================")
    print(f"       [!] ACTUALIZACIÓN DISPONIBLE EN GITHUB ({count} cambios)")
    print("========================================================================")
    print("Últimos cambios:")
    log_res = run_git_cmd(["log", "HEAD..origin/main", "--oneline", "-5"], timeout=10)
    print(log_res.stdout.strip())
    print("========================================================================")
    print("Seguridad garantizada: se creará un snapshot previo de whatsapp_backup.db.")
    print("")
    try:
        resp = input("¿Deseas aplicar la actualización ahora? (S/N) [S]: ").strip().upper()
    except Exception:
        resp = "N"

    if resp in ("", "S", "SI"):
        print("\n[1/3] Creando copias preventivas de seguridad de las bases de datos...")
        config.backup_all_databases()

        print("[2/3] Descargando código actualizado...")
        pull_res = run_git_cmd(["pull", "origin", "main"], timeout=30)
        if pull_res.returncode == 0:
            print("      Código actualizado correctamente.")
            print("[3/3] Verificando dependencias de Python...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "--quiet"], cwd=base_dir)
            print("\n" + "=" * 65)
            print("¡Actualización completada!")
            print("Reinicia el servidor (Ctrl+C y vuelve a abrir iniciar_visor.bat)")
            print("para que los cambios surtan efecto.")
            print("=" * 65)
            _last_update_status_text = f"[✓] Actualizado con éxito a la última versión ({datetime.now().strftime('%H:%M:%S')})"
            print("\nPresiona Enter para volver al Panel de Control...")
            try:
                input()
            except Exception:
                pass
        else:
            print(f"      [Error] Falló la descarga: {pull_res.stderr}")
            append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Update Pull Error] {pull_res.stderr}")
            _last_update_status_text = f"[Error] Falló la descarga de GitHub ({datetime.now().strftime('%H:%M:%S')})"
            print("\nPresiona Enter para volver al Panel de Control...")
            try:
                input()
            except Exception:
                pass
    else:
        _last_update_status_text = f"[Aviso] {count} actualización(es) pendiente(s) de aplicar"

    _update_in_progress = False
    render_dashboard(url)

def open_logs_window():
    """Abre la ventana dedicada de logs en una consola independiente."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    bat_path = os.path.join(base_dir, "ver_logs.bat")
    try:
        if os.name == "nt" and os.path.isfile(bat_path):
            subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif os.name == "nt":
            ps_cmd = (
                "$Host.UI.RawUI.WindowTitle = 'Logs de Sincronizacion - WAckup'; "
                f"Get-Content -Path '{SYNC_LOG_PATH}' -Wait -Tail 50"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
    except Exception:
        pass

def start_keyboard_listener(url):
    """Escucha teclas de atajo [V] y [L] en segundo plano."""
    if os.name != "nt":
        return
    try:
        import msvcrt

        def _listen():
            while True:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b"l", b"L"):
                            open_logs_window()
                        elif ch in (b"v", b"V"):
                            webbrowser.open(url)
                        elif ch in (b"u", b"U"):
                            check_and_apply_update(url)
                    time.sleep(0.15)
                except Exception:
                    time.sleep(0.5)

        t = threading.Thread(target=_listen, daemon=True)
        t.start()
    except Exception:
        pass

def log_sync_status(stats, account_id="default"):
    global _sync_batch_count, _total_messages_synced, _last_sync_status_text
    msgs = stats.get("messages_saved", 0)
    contacts = stats.get("contacts_saved", 0)
    chats = stats.get("chats_saved", 0)
    now_time = datetime.now().strftime("%H:%M:%S")
    now_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _sync_lock:
        _sync_batch_count += 1
        _total_messages_synced += msgs
        acc_tag = f" [{account_id}]" if account_id and account_id != "default" else ""
        _last_sync_status_text = (
            f"[{now_time}]{acc_tag} Lote #{_sync_batch_count} (+{msgs}) | "
            f"Msgs: {_total_messages_synced} | Chats: {chats} | Contactos: {contacts}"
        )

    # 1. Guardar log detallado en sync.log para la ventana de logs
    append_sync_log(
        f"[{now_full}] [Sync: {account_id}] Lote #{_sync_batch_count}: +{msgs} msgs, {chats} chats, {contacts} contactos | Detalle: {stats}"
    )

    # 2. Actualizar línea única en el panel de control si no hay actualización en curso
    with _ui_lock:
        if not _update_in_progress:
            status_text = f"  Estado: {_last_sync_status_text}"
            try:
                cols = shutil.get_terminal_size(fallback=(80, 24)).columns
                max_len = max(40, cols - 1)
            except Exception:
                max_len = 79

            if len(status_text) > max_len:
                status_text = status_text[:max_len]

            sys.stdout.write(f"\r{status_text.ljust(max_len)}")
            sys.stdout.flush()

class BackupServerHandler(BaseHTTPRequestHandler):
    def is_trusted_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed_origins = [
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
            "https://web.whatsapp.com"
        ]
        if origin in allowed_origins or origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
            return True
        return False

    def send_cors_headers(self):
        origin = self.headers.get("Origin")
        allowed_origins = [
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
            "https://web.whatsapp.com"
        ]
        if origin and (origin in allowed_origins or origin.startswith("chrome-extension://") or origin.startswith("moz-extension://")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With, Range")
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        if not self.is_trusted_origin():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def send_json(self, status_code, data):
        response_bytes = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(response_bytes)

    def _serve_file_with_range(self, filepath, mime_type):
        """Sirve un archivo soportando peticiones HTTP Range (206 Partial Content) y streaming por bloques."""
        file_size = os.path.getsize(filepath)
        range_header = self.headers.get("Range")

        start = 0
        end = file_size - 1
        status_code = 200

        if range_header:
            m = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if m:
                start_str, end_str = m.groups()
                if start_str:
                    start = int(start_str)
                if end_str:
                    end = min(int(end_str), file_size - 1)
                if start < file_size and start <= end:
                    status_code = 206

        length = end - start + 1
        self.send_response(status_code)
        self.send_header("Content-Type", mime_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_cors_headers()
        self.end_headers()

        chunk_size = 64 * 1024
        with open(filepath, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    break
                remaining -= len(chunk)

    def _resolve_account_params(self, params=None):
        account_id = None
        if params:
            account_id = params.get("account_id", [None])[0]
        if account_id and account_id != config.DEFAULT_ACCOUNT_ID:
            config.migrate_single_to_multi_account(account_id)
        elif not account_id or account_id == config.DEFAULT_ACCOUNT_ID:
            accounts = config.list_accounts()
            real_accounts = [a for a in accounts if a["id"] != config.DEFAULT_ACCOUNT_ID and a.get("total_messages", 0) > 0]
            if real_accounts:
                account_id = real_accounts[0]["id"]
        db_path = config.get_account_db(account_id)
        backup_dir = config.get_account_backup_dir(account_id)
        return account_id, db_path, backup_dir

    def do_GET(self):
        if not self.is_trusted_origin():
            self.send_json(403, {"error": "Origen no permitido"})
            return

        url = urlparse(self.path)

        if url.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_UI.encode("utf-8"))
            return

        elif url.path in ["/favicon.jpg", "/favicon.ico"]:
            favicon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "favicon.jpg")
            if os.path.isfile(favicon_path):
                with open(favicon_path, "rb") as f:
                    icon_bytes = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(icon_bytes)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(icon_bytes)
            else:
                self.send_response(404)
                self.end_headers()
            return

        elif url.path == "/api/accounts":
            accounts = config.list_accounts()
            self.send_json(200, accounts)
            return

        elif url.path == "/api/sync/status":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            include_ids = params.get("include_ids", ["0"])[0] == "1"
            status = db_manager.get_sync_status(db_path=db_path, include_ids=include_ids)
            status["account_id"] = account_id or config.DEFAULT_ACCOUNT_ID
            status["extension_version"] = get_disk_extension_version()
            self.send_json(200, status)
            return

        elif url.path == "/api/search":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            q = params.get("q", [""])[0]
            chat_id = params.get("chat_id", [None])[0]
            results = db_manager.search_full_text(q, chat_id=chat_id, limit=50, db_path=db_path)
            self.send_json(200, results)
            return

        elif url.path == "/api/chats":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            conn = db_manager.get_connection(db_path)
            cur = conn.cursor()
            contact_map = db_manager.build_contact_map(cur)
            query = """
            WITH latest_msgs AS (
                SELECT 
                    chat_id,
                    contenido AS last_msg,
                    tipo AS last_tipo,
                    timestamp AS last_ts,
                    ROW_NUMBER() OVER (PARTITION BY chat_id ORDER BY timestamp DESC) AS rn
                FROM mensajes
                WHERE chat_id IS NOT NULL 
                  AND chat_id != 'status@broadcast'
                  AND tipo NOT IN ('e2e_notification', 'notification_template', 'gp2', 'protocol', 'ciphertext', 'call_log', 'biz_content_placeholder', 'message_history_notice', 'pinned_message', 'album')
                  AND NOT (tipo = 'chat' AND (contenido IS NULL OR contenido = ''))
            )
            SELECT 
                lm.chat_id,
                COALESCE(
                    NULLIF(c.nombre, lm.chat_id),
                    ct.nombre,
                    ct_num.nombre,
                    ct.push_name,
                    ct_num.push_name,
                    c.nombre,
                    lm.chat_id
                ) AS display_name,
                COALESCE(
                    CASE WHEN lm.chat_id LIKE '%@c.us' OR lm.chat_id LIKE '%@s.whatsapp.net' THEN SUBSTR(lm.chat_id, 1, INSTR(lm.chat_id, '@') - 1) END,
                    ct.numero,
                    ct_num.numero
                ) AS phone_number,
                lm.last_msg,
                lm.last_tipo,
                COALESCE(c.ultimo_timestamp, lm.last_ts) AS last_ts
            FROM latest_msgs lm
            LEFT JOIN chats c ON lm.chat_id = c.chat_id
            LEFT JOIN contactos ct ON lm.chat_id = ct.contact_id
            LEFT JOIN contactos ct_num ON lm.chat_id LIKE '%@c.us' AND REPLACE(lm.chat_id, '@c.us', '@s.whatsapp.net') = ct_num.contact_id
            WHERE lm.rn = 1
            ORDER BY COALESCE(c.ultimo_timestamp, lm.last_ts) DESC
            """
            cur.execute(query)
            chats = [dict(row) for row in cur.fetchall()]
            conn.close()
            for ch in chats:
                if ch.get("last_msg"):
                    ch["last_msg"] = db_manager.resolve_mentions_in_text(ch["last_msg"], contact_map)
            self.send_json(200, chats)
            return

        elif url.path == "/api/messages":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            chat_id = params.get("chat_id", [None])[0]
            if not chat_id:
                self.send_json(400, {"error": "Falta parámetro chat_id"})
                return

            conn = db_manager.get_connection(db_path)
            cur = conn.cursor()
            contact_map = db_manager.build_contact_map(cur)
            query = """
            SELECT 
                m.msg_id,
                m.chat_id,
                m.es_mio,
                m.fecha_utc,
                m.timestamp,
                m.tipo,
                m.contenido,
                m.archivo_local,
                m.mimetype,
                m.tamano,
                m.duracion,
                m.thumbnail,
                m.estado_multimedia,
                m.quoted_msg_id,
                m.quoted_author,
                m.quoted_body,
                m.reenviado,
                COALESCE(ct.nombre, ct_num.nombre, ct.push_name, ct_num.push_name, m.remitente) AS remitente_nombre,
                m.remitente AS remitente_jid
            FROM mensajes m
            LEFT JOIN contactos ct ON m.remitente = ct.contact_id
            LEFT JOIN contactos ct_num ON REPLACE(m.remitente, '@c.us', '@s.whatsapp.net') = ct_num.contact_id
            WHERE m.chat_id = ?
              AND m.tipo NOT IN ('e2e_notification', 'notification_template', 'gp2', 'protocol', 'ciphertext', 'biz_content_placeholder')
            ORDER BY m.timestamp ASC
            """
            cur.execute(query, (chat_id,))
            messages = [dict(row) for row in cur.fetchall()]
            conn.close()

            # Mapa de mensajes del chat para auto-resolver mensajes citados si no traían el texto incrustado
            msg_map = {}
            for m in messages:
                mid = m.get("msg_id") or ""
                msg_map[mid] = m
                for p in mid.split("_"):
                    if len(p) >= 12:
                        msg_map[p] = m

            for m in messages:
                if m.get("contenido"):
                    m["contenido"] = db_manager.resolve_mentions_in_text(m["contenido"], contact_map)

                # Si falta quoted_body pero existe quoted_msg_id, resolverlo desde el mensaje original citado
                if not m.get("quoted_body") and m.get("quoted_msg_id"):
                    qid = m["quoted_msg_id"]
                    if qid in msg_map and msg_map[qid].get("contenido"):
                        m["quoted_body"] = msg_map[qid]["contenido"]
                    if not m.get("quoted_author") and qid in msg_map and msg_map[qid].get("remitente_nombre"):
                        m["quoted_author"] = msg_map[qid]["remitente_nombre"]

                if m.get("quoted_body"):
                    m["quoted_body"] = db_manager.resolve_mentions_in_text(m["quoted_body"], contact_map)
                qa = m.get("quoted_author")
                if qa:
                    qa_key = qa.split("@")[0]
                    if qa in contact_map:
                        m["quoted_author"] = contact_map[qa]
                    elif qa_key in contact_map:
                        m["quoted_author"] = contact_map[qa_key]
            self.send_json(200, messages)
            return

        elif url.path == "/api/export/html":
            params = parse_qs(url.query)
            _, db_path, _ = self._resolve_account_params(params)
            chat_id = params.get("chat_id", [None])[0]
            embed_media = params.get("embed", ["0"])[0] == "1"
            if not chat_id:
                self.send_json(400, {"error": "Falta parámetro chat_id"})
                return

            html_content = generate_chat_html_export(chat_id, embed_media=embed_media, db_path=db_path)
            html_bytes = html_content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="chat_{chat_id.split("@")[0]}.html"')
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        elif url.path == "/api/export/contacts":
            params = parse_qs(url.query)
            account_id, db_path, _ = self._resolve_account_params(params)
            contacts = db_manager.get_contacts_for_export(db_path=db_path)
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(["Nombre", "Número de teléfono"])
            for c in contacts:
                writer.writerow([c["nombre"], c["telefono"]])
            csv_bytes = ("\ufeff" + output.getvalue()).encode("utf-8")
            ts_str = datetime.now().strftime("%Y-%m-%d")
            acc_str = f"_{account_id}" if account_id else ""
            self.send_response(200)
            self.send_header("Content-type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="contactos_whatsapp{acc_str}_{ts_str}.csv"')
            self.send_header("Content-Length", str(len(csv_bytes)))
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        elif url.path == "/api/backup/list":
            params = parse_qs(url.query)
            _, _, backup_dir = self._resolve_account_params(params)
            backups = db_manager.list_manual_backups(backup_dir=backup_dir)
            self.send_json(200, backups)
            return

        elif url.path == "/api/backup/download":
            params = parse_qs(url.query)
            _, _, backup_dir = self._resolve_account_params(params)
            filename = os.path.basename(params.get("file", [""])[0])
            backups_root = os.path.abspath(backup_dir)
            filepath = os.path.abspath(os.path.join(backups_root, filename))
            if filepath.startswith(backups_root + os.sep) and os.path.isfile(filepath):
                self._serve_file_with_range(filepath, "application/octet-stream")
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        elif url.path.startswith("/media/") or (url.path.startswith("/data/") and "/media/" in url.path):
            decoded_path = unquote(url.path).lstrip("/")
            abs_file_path = os.path.abspath(os.path.join(BASE_DIR, decoded_path))
            data_root = os.path.abspath(DATA_DIR)
            legacy_media_root = os.path.abspath(os.path.join(BASE_DIR, "media"))

            # Fallback si se solicitó "/media/..." o "/data/media/..." directamente
            if not os.path.isfile(abs_file_path):
                sub_rel = None
                if decoded_path.startswith("media/"):
                    sub_rel = decoded_path[len("media/"):]
                elif decoded_path.startswith("data/media/"):
                    sub_rel = decoded_path[len("data/media/"):]

                if sub_rel:
                    cand = os.path.abspath(os.path.join(MEDIA_DIR, sub_rel))
                    if os.path.isfile(cand):
                        abs_file_path = cand
                    else:
                        for acc in config.list_accounts():
                            acc_media = os.path.join(acc["dir"], "media", sub_rel)
                            if os.path.isfile(acc_media):
                                abs_file_path = os.path.abspath(acc_media)
                                break

            # Prevenir vulnerabilidad de Path Traversal
            is_valid = (
                (abs_file_path.startswith(data_root + os.sep) or abs_file_path.startswith(legacy_media_root + os.sep))
                and os.path.isfile(abs_file_path)
            )

            if is_valid:
                mime, _ = mimetypes.guess_type(abs_file_path)
                mime = mime or "application/octet-stream"
                self._serve_file_with_range(abs_file_path, mime)
                return
            else:
                self.send_response(404)
                self.end_headers()
                return

        elif url.path == "/api/extension/info":
            ext_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "extension"))
            self.send_json(200, {
                "extension_path": ext_path,
                "browser_extension_url": "chrome://extensions",
                "extension_version": get_disk_extension_version()
            })
            return

        elif url.path == "/logs":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if os.path.isfile(SYNC_LOG_PATH):
                with open(SYNC_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.wfile.write("Aún no hay registros en logs de sincronización".encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        try:
            if not self.is_trusted_origin():
                self.send_json(403, {"error": "Origen no permitido"})
                return

            url = urlparse(self.path)

            if url.path == "/api/logs/open":
                open_logs_window()
                self.send_json(200, {"success": True, "message": "Ventana de logs abierta"})
                return

            elif url.path == "/api/sync":
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length == 0:
                    self.send_json(400, {"error": "Cuerpo de solicitud vacío"})
                    return

                body = self.rfile.read(content_length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except Exception as e:
                    self.send_json(400, {"error": f"JSON inválido: {e}"})
                    return

                account_id = payload.get("account_id")
                account_name = payload.get("account_name")
                account_phone = payload.get("account_phone")

                if account_id:
                    config.migrate_single_to_multi_account(account_id, name=account_name, phone=account_phone)

                db_path = config.get_account_db(account_id)
                config.save_account_metadata(account_id, name=account_name, phone=account_phone)
                db_manager.init_db(db_path=db_path)

                stats = db_manager.save_incremental_sync(payload, db_path=db_path)
                log_sync_status(stats, account_id=account_id or "default")
                media_manager.trigger_media_processing(db_path=db_path)

                self.send_json(200, {"success": True, "stats": stats, "account_id": account_id or "default"})
                return

            elif url.path == "/api/backup/create":
                params = parse_qs(url.query)
                _, db_path, backup_dir = self._resolve_account_params(params)
                backup_info = db_manager.create_manual_backup(db_path=db_path, backup_dir=backup_dir)
                self.send_json(200, {"success": True, "backup": backup_info})
                return

            elif url.path == "/api/media/process":
                params = parse_qs(url.query)
                _, db_path, _ = self._resolve_account_params(params)
                media_manager.trigger_media_processing(db_path=db_path)
                self.send_json(200, {"status": "en_proceso"})
                return

            elif url.path == "/api/media/retry":
                params = parse_qs(url.query)
                _, db_path, _ = self._resolve_account_params(params)
                reset_count = db_manager.retry_failed_media(db_path=db_path)
                media_manager.trigger_media_processing(db_path=db_path)
                self.send_json(200, {"success": True, "reset_count": reset_count})
                return

            self.send_response(404)
            self.end_headers()
        except Exception as e:
            traceback.print_exc()
            self.send_json(500, {"error": str(e)})

    def log_message(self, format, *args):
        return

def run():
    migrate_legacy_data()
    ensure_data_dirs()
    accounts = config.list_accounts()
    if accounts:
        for acc in accounts:
            if acc.get("db_path"):
                db_manager.init_db(acc["db_path"])
    else:
        db_manager.init_db()

    media_manager.ensure_media_dirs()
    media_manager.trigger_media_processing()

    server = ThreadingHTTPServer(("127.0.0.1", PORT), BackupServerHandler)
    url = f"http://127.0.0.1:{PORT}"

    append_sync_log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Servidor] Servidor iniciado en {url}")

    render_dashboard(url)

    start_keyboard_listener(url)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nServidor detenido correctamente.")
        server.server_close()

if __name__ == "__main__":
    run()