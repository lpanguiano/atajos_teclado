"""
Escáner de teclas en segundo plano con macros y hotstrings.
Compatible con Windows 11 Home.

Requisitos:
    pip install keyboard pyautogui

Formato de combinaciones.txt:
[
    { "tipo": "teclas", "teclas": "ctrl+shift+1", "ejecutar": "Juan;{TAB};..." },
    { "tipo": "texto",  "teclas": "sop",          "ejecutar": "soporte@empresa.com" }
]

En 'ejecutar':
    - Texto plano:               "Hola mundo"
    - Teclas especiales:         "{TAB}", "{ENTER}", "{F5}", "tab", "enter", "f5"
    - Hotkeys:                   "{CTRL+S}", "ctrl+shift+s", "alt+f4"
    - Múltiples acciones:        "Juan;{TAB};Perez;{TAB};{ENTER}"
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Dict, List, Tuple

import keyboard
import pyautogui


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "combinaciones.txt"

DEFAULT_DATA: List[Dict[str, str]] = [
    {"tipo": "teclas", "teclas": "ctrl+alt+h", "ejecutar": "Hola mundo"},
]

# Flag global: mientras se ejecuta un macro, los listeners ignoran eventos.
# Se usa threading.Event por ser thread-safe (los hooks de keyboard corren
# en hilos separados del hilo principal de la GUI).
_macro_en_ejecucion = threading.Event()

# Listeners con buffer interno que deben vaciarse al terminar un macro.
_listeners_con_buffer: List = []


def _registrar_listener_buffer(listener) -> None:
    _listeners_con_buffer.append(listener)


def _limpiar_buffers_listeners() -> None:
    for lst in _listeners_con_buffer:
        try:
            lst.limpiar_buffer()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------
def cargar_combinaciones() -> List[Dict[str, str]]:
    """Carga el archivo JSON. Si no existe, lo crea con datos por defecto."""
    if not CONFIG_FILE.exists():
        guardar_combinaciones(DEFAULT_DATA)
        return [dict(x) for x in DEFAULT_DATA]

    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("El JSON debe ser una lista de objetos.")

        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Cada elemento debe ser un objeto JSON.")
            if "teclas" not in item or "ejecutar" not in item:
                raise ValueError("Cada combinación debe tener 'teclas' y 'ejecutar'.")
            tipo = (item.get("tipo") or "teclas").lower()
            if tipo not in ("teclas", "texto"):
                raise ValueError(f"Tipo desconocido: '{tipo}' (usa 'teclas' o 'texto').")
            item["tipo"] = tipo
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        messagebox.showerror(
            "Error de formato",
            f"El archivo combinaciones.txt está corrupto:\n{exc}\n"
            "Se creará uno nuevo con valores por defecto.",
        )
        guardar_combinaciones(DEFAULT_DATA)
        return [dict(x) for x in DEFAULT_DATA]


def guardar_combinaciones(data: List[Dict[str, str]]) -> None:
    """Guarda la lista de combinaciones en disco."""
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Normalización de teclas
# ---------------------------------------------------------------------------
ALIAS_TECLAS = {
    # Modificadores
    "mayusculas": "shift", "mayúsculas": "shift", "shift": "shift",
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt", "altgr": "alt", "alt gr": "alt",
    "win": "win", "windows": "win", "super": "win",
    # Especiales
    "enter": "enter", "intro": "enter", "return": "enter",
    "esc": "esc", "escape": "esc",
    "tab": "tab", "tabulador": "tab",
    "space": "space", "espacio": "space",
    "backspace": "backspace", "borrar": "backspace", "retroceso": "backspace",
    "delete": "delete", "supr": "delete", "suprimir": "delete",
    "up": "up", "arriba": "up",
    "down": "down", "abajo": "down",
    "left": "left", "izquierda": "left",
    "right": "right", "derecha": "right",
    "home": "home", "inicio": "home",
    "end": "end", "fin": "end",
    "pageup": "pageup", "repag": "pageup", "re pag": "pageup",
    "pagedown": "pagedown", "avpag": "pagedown", "av pag": "pagedown",
    "insert": "insert", "insertar": "insert",
}

ESPECIALES_PYAUTOGUI = {
    "tab", "enter", "esc", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end",
    "pageup", "pagedown", "insert",
    "ctrl", "alt", "shift", "win",
}


def _normalizar_tecla(nombre: str) -> str:
    n = (nombre or "").strip().lower()
    if not n:
        return ""
    if n in ALIAS_TECLAS:
        return ALIAS_TECLAS[n]
    if n.startswith("f") and n[1:].isdigit():
        return n
    if len(n) == 1:
        return n
    return n


def _normalizar_nombre_hook(nombre: str) -> str:
    """Normaliza el nombre que devuelve keyboard.hook()."""
    n = (nombre or "").lower()
    mapa = {
        "left ctrl": "ctrl", "right ctrl": "ctrl", "ctrl derecha": "ctrl",
        "left shift": "shift", "right shift": "shift",
        "mayusculas": "shift", "mayúsculas": "shift", "shift derecha": "shift",
        "left alt": "alt", "right alt": "alt", "alt gr": "alt", "altgr": "alt",
        "windows": "win", "left windows": "win", "right windows": "win",
        " ": "space",
    }
    n = mapa.get(n, n)
    if len(n) == 1:
        n = n.lower()
    return n


# ---------------------------------------------------------------------------
# Liberación de modificadores (crítico para no escribir con Shift pegado)
# ---------------------------------------------------------------------------
def _esperar_soltar_modificadores(timeout: float = 1.0) -> None:
    modificadores = ("ctrl", "alt", "shift", "windows")
    inicio = time.time()
    while time.time() - inicio < timeout:
        if not any(keyboard.is_pressed(m) for m in modificadores):
            return
        time.sleep(0.01)


def _liberar_modificadores_real() -> None:
    for k in ("shift", "ctrl", "alt", "win"):
        try:
            pyautogui.keyUp(k)
        except Exception:  # noqa: BLE001
            pass
    for k in ("shift", "left shift", "right shift",
              "ctrl", "left ctrl", "right ctrl",
              "alt", "left alt", "right alt", "alt gr",
              "windows", "left windows", "right windows"):
        try:
            keyboard.release(k)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Parser de acciones para el campo 'ejecutar'
# ---------------------------------------------------------------------------
def _parsear_accion(accion: str) -> Tuple[str, str]:
    """
    Devuelve (tipo, valor):
      - ('texto', 'Hola mundo')
      - ('tecla', 'tab' / 'enter' / 'f5')
      - ('hotkey', 'ctrl+shift+s')
    """
    a = accion.strip()
    if not a:
        return ("", "")

    # {TAB}, {ENTER}, {F5}, {CTRL+S}
    if a.startswith("{") and a.endswith("}"):
        interior = a[1:-1].strip()
        if "+" in interior:
            return ("hotkey", interior)
        return ("tecla", _normalizar_tecla(interior))

    # ctrl+shift+s (sin llaves)
    if "+" in a:
        return ("hotkey", a)

    normalizada = _normalizar_tecla(a)
    es_f = normalizada.startswith("f") and normalizada[1:].isdigit()
    if normalizada in ESPECIALES_PYAUTOGUI or es_f:
        return ("tecla", normalizada)

    return ("texto", a)


def _ejecutar_accion(tipo: str, valor: str) -> None:
    if not tipo or not valor:
        return

    if tipo == "hotkey":
        partes = [_normalizar_tecla(p) for p in valor.split("+") if p.strip()]
        partes = [p for p in partes if p]
        if partes:
            try:
                pyautogui.hotkey(*partes)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERROR] hotkey {partes}: {exc}")

    elif tipo == "tecla":
        try:
            pyautogui.press(valor)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] press '{valor}': {exc}")

    elif tipo == "texto":
        # _LEO_ pyautogui.write(valor, interval=0.01)
        # keyboard.write maneja bien caracteres que requieren AltGr/Shift
        # (por ejemplo '@', '#', '~', acentos, 'ñ', etc.) según el layout.
        try:
            keyboard.write(valor, delay=0.01)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] write '{valor}': {exc}")
            # Fallback por si keyboard.write falla en algún layout raro
            pyautogui.write(valor, interval=0.01)


def ejecutar_macro(texto: str) -> None:
    """
    Ejecuta el campo 'ejecutar'. Las acciones se separan por ';'.

    Mientras corre, activa un flag global que hace que los listeners
    (hotkeys y hotstrings) ignoren los eventos generados por pyautogui.
    Esto evita bucles infinitos cuando el propio macro contiene texto
    que coincide con algún disparador.
    """
    if not texto:
        return

    # Si ya estamos dentro de un macro, no anidamos (evita recursión)
    if _macro_en_ejecucion.is_set():
        return

    _macro_en_ejecucion.set()
    try:
        # Esperar a que el usuario suelte modificadores antes de escribir.
        _esperar_soltar_modificadores(timeout=1.0)
        _liberar_modificadores_real()
        time.sleep(0.03)

        acciones = texto.split(";")
        for accion in acciones:
            if not accion.strip():
                continue
            tipo, valor = _parsear_accion(accion)
            _ejecutar_accion(tipo, valor)
            time.sleep(0.05)

        # Pausa adaptativa al tamaño del macro:
        # 150 ms para macros cortos, hasta 1 s para macros largos.
        pausa = max(0.15, min(1.0, len(texto) * 0.01))
        time.sleep(pausa)
    finally:
        # Limpiamos buffers residuales (por si algún evento alcanzó a entrar
        # justo antes de que se activara el flag) y liberamos el escudo.
        _limpiar_buffers_listeners()
        _macro_en_ejecucion.clear()


# ---------------------------------------------------------------------------
# Listener de hotkeys (combinaciones de teclas)
# ---------------------------------------------------------------------------
def _tiene_modificador(teclas: str) -> bool:
    t = (teclas or "").lower()
    return any(m in t for m in ("ctrl", "alt", "shift", "win", "mayusculas", "mayúsculas"))


class ListenerMacros:
    """Escucha combinaciones de teclas y dispara los macros asociados."""

    def __init__(self) -> None:
        self._activo = False
        self._hotkeys: List = []

    @property
    def activo(self) -> bool:
        return self._activo

    def iniciar(self, combinaciones: List[Dict[str, str]]) -> None:
        if self._activo:
            return

        for combo in combinaciones:
            tipo = (combo.get("tipo") or "teclas").lower()
            if tipo != "teclas":
                continue

            teclas = (combo.get("teclas") or "").strip()
            ejecutar = combo.get("ejecutar", "")
            if not teclas:
                continue

            partes_norm = []
            for p in teclas.split("+"):
                p = p.strip()
                if not p:
                    continue
                p_norm = _normalizar_tecla(p)
                if len(p_norm) == 1:
                    p_norm = p_norm.lower()
                partes_norm.append(p_norm)
            teclas_norm = "+".join(partes_norm)

            def _callback(t=ejecutar):
                # Si un macro está corriendo, ignoramos este disparo.
                if _macro_en_ejecucion.is_set():
                    return
                threading.Thread(
                    target=ejecutar_macro, args=(t,), daemon=True
                ).start()

            try:
                hk = keyboard.add_hotkey(
                    teclas_norm,
                    _callback,
                    suppress=_tiene_modificador(teclas_norm),
                    trigger_on_release=False,
                )
                self._hotkeys.append(hk)
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] Combinación inválida '{teclas}' -> '{teclas_norm}': {exc}")

        self._activo = True

    def detener(self) -> None:
        if not self._activo:
            return
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:  # noqa: BLE001
            pass
        self._hotkeys.clear()
        self._activo = False


# ---------------------------------------------------------------------------
# Listener de hotstrings (secuencias de texto)
# ---------------------------------------------------------------------------
class ListenerTextos:
    """
    Escucha todas las teclas, mantiene un buffer de las últimas N letras
    y, cuando el buffer termina en alguna de las secuencias configuradas,
    borra la secuencia escrita y ejecuta el macro asociado.

    Ignora eventos mientras un macro está en ejecución (flag global).
    """

    RESET_KEYS = {
        "space", "enter", "tab", "esc",
        "delete",
        "up", "down", "left", "right",
        "home", "end", "pageup", "pagedown", "insert",
    }

    def __init__(self) -> None:
        self._activo = False
        self._hook = None
        self._buffer = ""
        self._secuencias: Dict[str, str] = {}
        self._max_len = 0
        self._lock = threading.Lock()
        _registrar_listener_buffer(self)

    @property
    def activo(self) -> bool:
        return self._activo

    def limpiar_buffer(self) -> None:
        """Vacía el buffer interno. Llamado al terminar un macro."""
        with self._lock:
            self._buffer = ""

    def iniciar(self, combinaciones: List[Dict[str, str]]) -> None:
        if self._activo:
            return

        self._secuencias.clear()
        for combo in combinaciones:
            tipo = (combo.get("tipo") or "teclas").lower()
            if tipo != "texto":
                continue
            teclas = (combo.get("teclas") or "").strip().lower()
            ejecutar = combo.get("ejecutar", "")
            if not teclas or not ejecutar:
                continue
            self._secuencias[teclas] = ejecutar

        if not self._secuencias:
            return

        self._max_len = max(len(s) for s in self._secuencias.keys()) + 5
        self._buffer = ""
        self._hook = keyboard.hook(self._on_event, suppress=False)
        self._activo = True

    def detener(self) -> None:
        if not self._activo:
            return
        try:
            if self._hook is not None:
                keyboard.unhook(self._hook)
        except Exception:  # noqa: BLE001
            pass
        self._hook = None
        self._buffer = ""
        self._activo = False

    # ------------------------------------------------------------------
    def _on_event(self, event) -> None:
        # Escudo: ignoramos eventos generados por el propio macro.
        if _macro_en_ejecucion.is_set():
            return

        if event.event_type != keyboard.KEY_DOWN:
            return

        nombre = _normalizar_nombre_hook(event.name)

        # Ignoramos modificadores solos
        if nombre in ("ctrl", "shift", "alt", "win"):
            return

        # Backspace quita un carácter del buffer
        if nombre == "backspace":
            with self._lock:
                self._buffer = self._buffer[:-1]
            return

        # Otras teclas especiales reinician el buffer
        if nombre in self.RESET_KEYS:
            with self._lock:
                self._buffer = ""
            return

        # Teclas de función reinician
        if nombre.startswith("f") and nombre[1:].isdigit():
            with self._lock:
                self._buffer = ""
            return

        # Solo aceptamos una letra o dígito
        if len(nombre) != 1 or not nombre.isalnum():
            return

        with self._lock:
            self._buffer += nombre.lower()
            if len(self._buffer) > self._max_len:
                self._buffer = self._buffer[-self._max_len:]

            for secuencia, ejecutar in self._secuencias.items():
                if self._buffer.endswith(secuencia):
                    self._buffer = ""
                    threading.Thread(
                        target=self._expandir,
                        args=(secuencia, ejecutar),
                        daemon=True,
                    ).start()
                    return

    # ------------------------------------------------------------------
    def _expandir(self, secuencia: str, ejecutar: str) -> None:
        """Borra la secuencia escrita y ejecuta el macro asociado."""
        # Si ya hay un macro corriendo, abortamos.
        if _macro_en_ejecucion.is_set():
            return

        time.sleep(0.05)

        # Borramos la secuencia original
        for _ in range(len(secuencia)):
            try:
                pyautogui.press("backspace")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.01)

        time.sleep(0.03)
        ejecutar_macro(ejecutar)


# ---------------------------------------------------------------------------
# Capturador de teclas para el campo "Teclas"
# ---------------------------------------------------------------------------
class CapturadorTeclas:
    """Escucha teclas y compone una combinación tipo 'ctrl+shift+s' o 'f9'."""

    MODIFICADORES = {"ctrl", "shift", "alt", "win"}

    def __init__(self, al_capturar) -> None:
        self._al_capturar = al_capturar
        self._activos: set = set()
        self._hook = None
        self._activo = False

    @property
    def activo(self) -> bool:
        return self._activo

    def iniciar(self) -> None:
        if self._activo:
            return
        self._activos.clear()
        self._hook = keyboard.hook(self._on_event, suppress=False)
        self._activo = True

    def detener(self) -> None:
        if not self._activo:
            return
        try:
            if self._hook is not None:
                keyboard.unhook(self._hook)
        except Exception:  # noqa: BLE001
            pass
        self._hook = None
        self._activos.clear()
        self._activo = False

    def _on_event(self, event) -> None:
        # No capturamos mientras corre un macro
        if _macro_en_ejecucion.is_set():
            return

        nombre = _normalizar_nombre_hook(event.name)

        if event.event_type == keyboard.KEY_DOWN:
            if nombre in self.MODIFICADORES:
                self._activos.add(nombre)
                return

            partes = []
            for mod in ("ctrl", "alt", "shift", "win"):
                if mod in self._activos:
                    partes.append(mod)
            partes.append(nombre)
            combinacion = "+".join(partes)

            threading.Thread(
                target=self._al_capturar,
                args=(combinacion,),
                daemon=True,
            ).start()

        elif event.event_type == keyboard.KEY_UP:
            self._activos.discard(nombre)


# ---------------------------------------------------------------------------
# Interfaz gráfica
# ---------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Escáner de Teclas · Macros y Textos")
        self.geometry("720x520")
        self.minsize(660, 480)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.combinaciones: List[Dict[str, str]] = cargar_combinaciones()
        self.listener = ListenerMacros()
        self.listener_textos = ListenerTextos()
        self.capturador = CapturadorTeclas(self._on_teclas_capturadas)
        self._mini = None

        self._construir_ui()
        self._refrescar_tabla()

    # ---------------- UI ----------------
    def _construir_ui(self) -> None:
        contenedor = ttk.Frame(self, padding=10)
        contenedor.pack(fill="both", expand=True)

        # --- Tabla ---
        columnas = ("tipo", "teclas", "ejecutar")
        self.tabla = ttk.Treeview(
            contenedor, columns=columnas, show="headings", height=12
        )
        self.tabla.heading("tipo", text="Tipo")
        self.tabla.heading("teclas", text="Combinación / Secuencia")
        self.tabla.heading("ejecutar", text="Macro a ejecutar")
        self.tabla.column("tipo", width=80, anchor="center")
        self.tabla.column("teclas", width=220, anchor="w")
        self.tabla.column("ejecutar", width=380, anchor="w")
        self.tabla.pack(fill="both", expand=True, pady=(0, 10))

        # --- Formulario ---
        form = ttk.LabelFrame(contenedor, text="Agregar / editar", padding=10)
        form.pack(fill="x", pady=(0, 10))

        ttk.Label(form, text="Tipo:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.cmb_tipo = ttk.Combobox(
            form, values=("teclas", "texto"), state="readonly", width=8
        )
        self.cmb_tipo.set("teclas")
        self.cmb_tipo.grid(row=0, column=1, sticky="w", padx=(0, 12))

        ttk.Label(form, text="Teclas / Secuencia:").grid(
            row=0, column=2, sticky="w", padx=(0, 6)
        )
        self.entry_teclas = ttk.Entry(form, width=20)
        self.entry_teclas.grid(row=0, column=3, sticky="we")

        self.btn_capturar = ttk.Button(
            form, text="🎯 Capturar", width=12, command=self._toggle_captura
        )
        self.btn_capturar.grid(row=0, column=4, padx=(6, 12))

        ttk.Label(form, text="Ejecutar:").grid(row=0, column=5, sticky="w", padx=(0, 6))
        self.entry_ejecutar = ttk.Entry(form, width=26)
        self.entry_ejecutar.grid(row=0, column=6, sticky="we")

        form.columnconfigure(3, weight=1)
        form.columnconfigure(6, weight=2)

        botones_form = ttk.Frame(form)
        botones_form.grid(row=1, column=0, columnspan=7, sticky="e", pady=(8, 0))
        ttk.Button(botones_form, text="➕ Agregar", command=self._agregar).pack(side="left", padx=4)
        ttk.Button(botones_form, text="✏️ Editar seleccionada", command=self._editar).pack(side="left", padx=4)
        ttk.Button(botones_form, text="🗑 Eliminar seleccionada", command=self._eliminar).pack(side="left", padx=4)

        # --- Controles inferiores ---
        controles = ttk.Frame(contenedor)
        controles.pack(fill="x")

        self.btn_escucha = ttk.Button(
            controles, text="▶ Iniciar escucha", command=self._toggle_escucha
        )
        self.btn_escucha.pack(side="left")

        self.btn_segundo_plano = ttk.Button(
            controles, text="⬇ Enviar a segundo plano", command=self._a_segundo_plano
        )
        self.btn_segundo_plano.pack(side="left", padx=6)

        self.lbl_estado = ttk.Label(controles, text="Estado: detenido", foreground="red")
        self.lbl_estado.pack(side="right")

        # --- Ayuda ---
        ayuda = (
            "Tipo 'teclas': se dispara con una combinación (ctrl+shift+1, shift+d, f9...).\n"
            "Tipo 'texto':  se dispara al escribir la secuencia (sop, mail, firma...).\n"
            "En 'Ejecutar': separa acciones con ';'. Usa {TAB}, {ENTER}, {F5}, {CTRL+S}.\n"
            "Ej: Juan;{TAB};Perez;{TAB};{ENTER}"
        )
        ttk.Label(
            contenedor, text=ayuda, foreground="#555", justify="left"
        ).pack(fill="x", pady=(8, 0))

    # ---------------- Utilidades ----------------
    def _refrescar_tabla(self) -> None:
        for item in self.tabla.get_children():
            self.tabla.delete(item)
        for combo in self.combinaciones:
            self.tabla.insert(
                "", "end",
                values=(
                    combo.get("tipo", "teclas"),
                    combo.get("teclas", ""),
                    combo.get("ejecutar", ""),
                ),
            )

    def _seleccion_actual(self):
        seleccion = self.tabla.selection()
        if not seleccion:
            return None
        return self.tabla.index(seleccion[0])

    def _recargar_listeners_si_activos(self) -> None:
        activos = self.listener.activo or self.listener_textos.activo
        if not activos:
            return
        self.listener.detener()
        self.listener_textos.detener()
        self.listener.iniciar(self.combinaciones)
        self.listener_textos.iniciar(self.combinaciones)

    # ---------------- CRUD ----------------
    def _agregar(self) -> None:
        tipo = self.cmb_tipo.get().strip().lower() or "teclas"
        teclas = self.entry_teclas.get().strip()
        ejecutar = self.entry_ejecutar.get().strip()
        if not teclas or not ejecutar:
            messagebox.showwarning("Datos incompletos", "Completá ambos campos.")
            return
        if any(c["teclas"] == teclas and c.get("tipo") == tipo for c in self.combinaciones):
            messagebox.showwarning(
                "Duplicado", f"Ya existe '{teclas}' de tipo '{tipo}'."
            )
            return

        self.combinaciones.append({"tipo": tipo, "teclas": teclas, "ejecutar": ejecutar})
        guardar_combinaciones(self.combinaciones)
        self._refrescar_tabla()
        self._recargar_listeners_si_activos()
        self.entry_teclas.delete(0, tk.END)
        self.entry_ejecutar.delete(0, tk.END)

    def _editar(self) -> None:
        idx = self._seleccion_actual()
        if idx is None:
            messagebox.showinfo("Sin selección", "Seleccioná una fila para editar.")
            return
        tipo = self.cmb_tipo.get().strip().lower() or "teclas"
        teclas = self.entry_teclas.get().strip()
        ejecutar = self.entry_ejecutar.get().strip()
        if not teclas or not ejecutar:
            messagebox.showwarning("Datos incompletos", "Completá ambos campos.")
            return

        self.combinaciones[idx] = {"tipo": tipo, "teclas": teclas, "ejecutar": ejecutar}
        guardar_combinaciones(self.combinaciones)
        self._refrescar_tabla()
        self._recargar_listeners_si_activos()

    def _eliminar(self) -> None:
        idx = self._seleccion_actual()
        if idx is None:
            messagebox.showinfo("Sin selección", "Seleccioná una fila para eliminar.")
            return
        teclas = self.combinaciones[idx]["teclas"]
        if not messagebox.askyesno("Confirmar", f"¿Eliminar '{teclas}'?"):
            return
        del self.combinaciones[idx]
        guardar_combinaciones(self.combinaciones)
        self._refrescar_tabla()
        self._recargar_listeners_si_activos()

    # ---------------- Escucha ----------------
    def _toggle_escucha(self) -> None:
        activo = self.listener.activo or self.listener_textos.activo
        if activo:
            self.listener.detener()
            self.listener_textos.detener()
            self.btn_escucha.config(text="▶ Iniciar escucha")
            self.lbl_estado.config(text="Estado: detenido", foreground="red")
        else:
            self.listener.iniciar(self.combinaciones)
            self.listener_textos.iniciar(self.combinaciones)
            self.btn_escucha.config(text="⏸ Detener escucha")
            self.lbl_estado.config(text="Estado: escuchando", foreground="green")

    # ---------------- Captura de teclas ----------------
    def _toggle_captura(self) -> None:
        if self.capturador.activo:
            self._detener_captura()
        else:
            self._iniciar_captura()

    def _iniciar_captura(self) -> None:
        # La captura solo aplica a tipo 'teclas'
        self.cmb_tipo.set("teclas")
        self.entry_teclas.delete(0, tk.END)
        self.entry_teclas.insert(0, "Pulsa la combinación…")
        self.entry_teclas.config(foreground="gray")
        self.btn_capturar.config(text="⏹ Cancelar")
        self.capturador.iniciar()
        self.lbl_estado.config(text="Estado: capturando teclas…", foreground="blue")

    def _detener_captura(self) -> None:
        self.capturador.detener()
        self.btn_capturar.config(text="🎯 Capturar")
        if self.listener.activo or self.listener_textos.activo:
            self.lbl_estado.config(text="Estado: escuchando", foreground="green")
        else:
            self.lbl_estado.config(text="Estado: detenido", foreground="red")

    def _on_teclas_capturadas(self, combinacion: str) -> None:
        self.after(0, self._aplicar_captura, combinacion)

    def _aplicar_captura(self, combinacion: str) -> None:
        self.entry_teclas.config(foreground="black")
        self.entry_teclas.delete(0, tk.END)
        self.entry_teclas.insert(0, combinacion)
        self._detener_captura()

    # ---------------- Segundo plano ----------------
    def _a_segundo_plano(self) -> None:
        if not (self.listener.activo or self.listener_textos.activo):
            self.listener.iniciar(self.combinaciones)
            self.listener_textos.iniciar(self.combinaciones)
            self.btn_escucha.config(text="⏸ Detener escucha")
            self.lbl_estado.config(text="Estado: escuchando", foreground="green")

        self.iconify()
        self.withdraw()
        self._crear_bandeja_flotante()

    def _crear_bandeja_flotante(self) -> None:
        if self._mini is not None and self._mini.winfo_exists():
            return

        self._mini = tk.Toplevel(self)
        self._mini.title("Macros activas")
        self._mini.geometry("200x70+20+20")
        self._mini.attributes("-topmost", True)
        self._mini.protocol("WM_DELETE_WINDOW", self._mostrar_ventana)

        ttk.Label(self._mini, text="Escáner activo 🎯").pack(pady=4)
        ttk.Button(self._mini, text="Mostrar ventana", command=self._mostrar_ventana).pack()

    def _mostrar_ventana(self) -> None:
        if self._mini is not None and self._mini.winfo_exists():
            self._mini.destroy()
        self._mini = None
        self.deiconify()
        self.state("normal")
        self.lift()

    # ---------------- Cierre ----------------
    def _on_close(self) -> None:
        try:
            self.capturador.detener()
            self.listener.detener()
            self.listener_textos.detener()
        finally:
            self.destroy()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.0
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
