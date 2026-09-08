import os
import requests
import tkinter as tk
from tkinter import filedialog
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TCON, APIC, error

def select_folder():
    """Opens a dialog to select the target directory."""
    root = tk.Tk()
    root.withdraw() # Hide the main tkinter window
    folder_path = filedialog.askdirectory(title="Select Folder with MP3s")
    return folder_path

def search_soundcloud(query):
    """Uses yt-dlp to search soundcloud and extract metadata."""
    print(f"Searching SoundCloud for: {query}...")
    
    ydl_opts = {
        'extract_flat': False,
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # scsearch1: limits the search to the top 1 result on SoundCloud
            info = ydl.extract_info(f"scsearch1:{query}", download=False)
            
            if 'entries' in info and len(info['entries']) > 0:
                track = info['entries'][0]
                return {
                    'artist': track.get('uploader'),
                    'genre': track.get('genre'),
                    'artwork_url': track.get('thumbnail')
                }
    except Exception as e:
        print(f"  [!] Failed to scrape data for {query}: {e}")
        
    return None

def process_mp3_files(folder_path):
    """Iterates through MP3s and updates missing metadata."""
    if not folder_path:
        print("No folder selected. Exiting.")
        return

    for filename in os.listdir(folder_path):
        if not filename.lower().endswith('.mp3'):
            continue
            
        filepath = os.path.join(folder_path, filename)
        track_name_from_file = os.path.splitext(filename)[0]
        
        print(f"\nProcessing: {filename}")
        
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
            print("  [-] File already has Artist, Genre, and Artwork. Skipping.")
            continue
            
        # If anything is missing, scrape SoundCloud
        sc_data = search_soundcloud(track_name_from_file)
        
        if not sc_data:
            print("  [!] No results found on SoundCloud.")
            continue
            
        needs_saving = False

        # 1. Update Artist
        if not has_artist and sc_data.get('artist'):
            print(f"  [+] Adding Artist: {sc_data['artist']}")
            audio.tags.add(TPE1(encoding=3, text=sc_data['artist']))
            needs_saving = True

        # 2. Update Genre
        if not has_genre and sc_data.get('genre'):
            print(f"  [+] Adding Genre: {sc_data['genre']}")
            audio.tags.add(TCON(encoding=3, text=sc_data['genre']))
            needs_saving = True

        # 3. Update Artwork
        if not has_artwork and sc_data.get('artwork_url'):
            print(f"  [+] Downloading and adding Artwork...")
            try:
                img_response = requests.get(sc_data['artwork_url'])
                if img_response.status_code == 200:
                    audio.tags.add(
                        APIC(
                            encoding=3,       # utf-8
                            mime='image/jpeg',
                            type=3,           # 3 is for album front cover
                            desc='Cover',
                            data=img_response.content
                        )
                    )
                    needs_saving = True
            except Exception as e:
                print(f"  [!] Failed to add artwork: {e}")

        # Save only if changes were made
        if needs_saving:
            audio.save()
            print("  [*] File updated successfully.")
        else:
            print("  [-] No missing data could be found to update.")

if __name__ == "__main__":
    target_folder = select_folder()
    process_mp3_files(target_folder)
    print("\nDone!")