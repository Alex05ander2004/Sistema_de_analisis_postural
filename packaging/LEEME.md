# Ejecutable para los participantes

## Construir

```bash
.venv\Scripts\python -m pip install pyinstaller==6.11.1 backports.tarfile==1.2.0
.venv\Scripts\python -m PyInstaller packaging\MonitorPostural.spec --noconfirm --distpath build\dist --workpath build\work
```

Resultado: `build\dist\MonitorPostural\` (~390 MB), con `MonitorPostural.exe`
dentro. Es una **carpeta**, no un archivo único: arranca en segundos y el
antivirus la marca mucho menos que un `.exe` autoextraíble.

Antes de construir la versión del estudio: subir `APP_VERSION` en
`src/version.py`, hacer commit y `git tag estudio-v1`.

## Entregar

1. Comprimir la carpeta `build\dist\MonitorPostural` en un `.zip`.
2. En el equipo del participante, descomprimirla en **Documentos**
   (no en el Escritorio de OneDrive ni en Descargas).
3. Crear un acceso directo a `MonitorPostural.exe` en el Escritorio.
4. Primer arranque: Windows SmartScreen mostrará *"Windows protegió su PC"*
   porque el ejecutable no está firmado. **Más información → Ejecutar de todas
   formas.** Hazlo tú en la instalación, no lo dejes al participante.
5. Configurar con el botón ⚙ Estudio (código, modo, FOV). Ver
   `docs/protocolo_estudio.md` §4.

## Dónde quedan los datos

`%LOCALAPPDATA%\MonitorPostural\` (escribe esa ruta en la barra del
Explorador):

| Archivo | Qué es |
|---|---|
| `history.db` | Bitácora (mediciones, alertas, autorreporte) |
| `estudio_local.json` | Código, modo y FOV de ese equipo |
| `logs\app.log` | Registro técnico (cámara perdida, errores) |

Borrar o reinstalar la carpeta del programa **no borra los datos**. El
participante los envía con **Exportar mis datos**, que incluye también el log.

## Problemas conocidos de empaquetado (ya resueltos en la receta)

- `No module named 'backports'`: setuptools recientes lo necesitan y
  PyInstaller no lo incluye. Se instala `backports.tarfile`.
- `No module named 'numpy._core._exceptions'`: numpy 2.x carga submódulos de
  forma dinámica; la receta los añade con `collect_submodules`.
- Sin `flet/bin` en el paquete, Flet descargaría su cliente de ventana de
  internet en el primer arranque.
