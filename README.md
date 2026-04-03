# File Output Render Tokens

A Blender addon that brings dynamic render tokens to **File Output nodes** and the **render filepath**. Set up your output paths once using tokens — they resolve automatically at render time.

## Features

- Tokens resolve in File Output nodes and the render output filepath
- Live **Path Preview** in the Compositor sidebar and Output Properties
- **Version control** with one-click increment/decrement
- **Token Presets** for fast node setup (Beauty, Cryptomatte, AOV)
- **Rename any token** to match your studio's naming convention
- Compatible with Blender 3.0 and later

## Installation

1. Download `file_output_tokens_v1.0.0.zip`
2. Open Blender → **Edit → Preferences → Add-ons**
3. Click **Install** and select the ZIP file
4. Enable **File Output Render Tokens**

## Tokens

### Project
| Token | Resolves to |
|---|---|
| `$prj` | Project filename (no extension) |
| `$camera` | Active camera name |
| `$viewlayer` | Active view layer name |
| `$take` | Scene name |
| `$pass` | Render pass input name |
| `$frame` | Current frame, zero-padded (0001) |
| `$res` | Resolution e.g. 1920x1080 |
| `$range` | Frame range e.g. 1-250 |
| `$fps` | Frame rate |
| `$version` | Version number, zero-padded (001) |

### Date / Time
| Token | Resolves to |
|---|---|
| `$YYYY` | Year (4-digit) |
| `$YY` | Year (2-digit) |
| `$MM` | Month (01-12) |
| `$DD` | Day (01-31) |
| `$hh` | Hour (00-23) |
| `$mm` | Minute (00-59) |
| `$ss` | Second (00-59) |

### System
| Token | Resolves to |
|---|---|
| `$Author` | Author (stamp note or OS user) |
| `$Username` | OS username |
| `$Computer` | Computer hostname |
| `$Renderer` | Render engine (e.g. Cycles, EEVEE) |
| `$Height` | Render height e.g. 1080p |

## Default Presets

| Name | Directory | File |
|---|---|---|
| Beauty | `//Export/$prj/$version/$camera/Beauty/` | `$camera_$version_Beauty_` |
| Cryptomatte | `//Export/$prj/$version/$camera/Cryptomatte/` | `$camera_$version_Cryptomatte_` |
| AOV | `//Export/$prj/$version/$camera/AOV/` | `$camera_$version_$pass_` |

## Renaming Tokens

Go to **Edit → Preferences → Add-ons → File Output Render Tokens → Show & Rename Tokens** to assign custom names to any token. Useful for studio pipelines with fixed naming conventions.

## License

MIT — see [LICENSE](LICENSE)
