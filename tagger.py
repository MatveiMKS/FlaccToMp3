import os
import requests
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TPE1, TCON, APIC, error

def search_soundcloud(query):
    """Uses yt-dlp to search soundcloud and extract metadata."""
    ydl_opts = {
        'extract_flat': False,
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        # Metadata only: don't fail on DRM-protected or otherwise unplayable tracks
        'ignore_no_formats_error': True
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # scsearch1: limits the search to the top 1 result on SoundCloud
        info = ydl.extract_info(f"scsearch1:{query}", download=False)

        if info and info.get('entries'):
            track = info['entries'][0]
            return {
                'artist': track.get('uploader'),
                'genre': track.get('genre'),
                'artwork_url': track.get('thumbnail')
            }

    return None

def tag_mp3(filepath):
    """Fills in missing Artist, Genre and Artwork on one MP3 from SoundCloud.

    Returns a short human-readable summary of what happened.
    Raises on search/network/file errors.
    """
    track_name_from_file = os.path.splitext(os.path.basename(filepath))[0]

    # Load MP3 file and initialize ID3 tags if missing
    audio = MP3(filepath, ID3=ID3)
    try:
        audio.add_tags()
    except error:
        pass # Tags already exist

    # Check existing metadata
    has_artist = 'TPE1' in audio.tags and str(audio.tags['TPE1']).strip() != ''
    has_genre = 'TCON' in audio.tags and str(audio.tags['TCON']).strip() != ''
    has_artwork = any(tag.startswith('APIC') for tag in audio.tags.keys())

    if has_artist and has_genre and has_artwork:
        return "already has Artist, Genre and Artwork"

    # If anything is missing, scrape SoundCloud
    sc_data = search_soundcloud(track_name_from_file)

    if not sc_data:
        return "no results found on SoundCloud"

    added = []

    # 1. Update Artist
    if not has_artist and sc_data.get('artist'):
        audio.tags.add(TPE1(encoding=3, text=sc_data['artist']))
        added.append(f"Artist ({sc_data['artist']})")

    # 2. Update Genre
    if not has_genre and sc_data.get('genre'):
        audio.tags.add(TCON(encoding=3, text=sc_data['genre']))
        added.append(f"Genre ({sc_data['genre']})")

    # 3. Update Artwork
    if not has_artwork and sc_data.get('artwork_url'):
        img_response = requests.get(sc_data['artwork_url'], timeout=30)
        if img_response.status_code == 200:
            mime = img_response.headers.get('Content-Type', 'image/jpeg').split(';')[0]
            audio.tags.add(
                APIC(
                    encoding=3,       # utf-8
                    mime=mime,
                    type=3,           # 3 is for album front cover
                    desc='Cover',
                    data=img_response.content
                )
            )
            added.append("Artwork")

    # Save only if changes were made
    if added:
        audio.save()
        return "added " + ", ".join(added)

    return "no missing data could be found"

def process_mp3_files(folder_path):
    """Iterates through MP3s and updates missing metadata."""
    if not folder_path:
        print("No folder selected. Exiting.")
        return

    for filename in os.listdir(folder_path):
        if not filename.lower().endswith('.mp3'):
            continue

        print(f"\nProcessing: {filename}")
        try:
            print(f"  [*] {tag_mp3(os.path.join(folder_path, filename))}")
        except Exception as e:
            print(f"  [!] Failed: {e}")

if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw() # Hide the main tkinter window
    target_folder = filedialog.askdirectory(title="Select Folder with MP3s")
    root.destroy()

    process_mp3_files(target_folder)
    print("\nDone!")
