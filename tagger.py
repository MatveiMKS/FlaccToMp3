import os
import re
import unicodedata
from difflib import SequenceMatcher

import requests
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TPE1, TCON, APIC, error

# How many SoundCloud results are compared against the file
SEARCH_RESULTS = 5
# Below this similarity (0..1) a result is considered a different track
MIN_SCORE = 0.6

YDL_OPTS = {
    'skip_download': True,
    'quiet': True,
    'no_warnings': True,
    'noplaylist': True,
    # Metadata only: don't fail on DRM-protected or otherwise unplayable tracks
    'ignore_no_formats_error': True
}

# Leading track numbers: "01 - ", "01. ", "1-02 ", "A1 - ", "03 ". A bare "99 " is kept: it may start the title
TRACK_NUMBER = re.compile(r'^\s*(?:\d{1,2}[-.]\d{1,2}(?:\s*[-._)]+\s*|\s+)|[a-d]?\d{1,3}\s*[-._)]+\s*|0\d\s+)', re.I)
# Bracketed text that never belongs to the track name
NOISE_BRACKETS = re.compile(
    r'[\(\[\{][^\)\]\}]*\b(?:free|download|dl|official|audio|video|lyrics?|premiere|out now|hq|hd|'
    r'\d{2,4}\s*kbps|explicit|clip|visuali[sz]er|original mix)\b[^\)\]\}]*[\)\]\}]', re.I)
# "feat. X" / "ft. X", bracketed or trailing
FEATURING = re.compile(r'[\(\[]\s*(?:feat|ft|featuring)\b\.?[^\)\]]*[\)\]]|\s(?:feat|ft|featuring)\b\.?\s.*$', re.I)

def clean_name(name):
    """Strips track numbers and download-site noise from a file name or track title."""
    name = name.replace('_', ' ')
    name = TRACK_NUMBER.sub('', name, count=1)
    name = NOISE_BRACKETS.sub(' ', name)
    return re.sub(r'\s+', ' ', name).strip(' -') or name.strip()

def normalize(text):
    """Lowercase, accent-free, punctuation-free form used to compare two names."""
    text = FEATURING.sub(' ', NOISE_BRACKETS.sub(' ', text or ''))
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    return ' '.join(re.sub(r'[^\w\s]', ' ', text).split())

def similarity(a, b):
    """0..1 similarity of two normalized names, tolerant of word order."""
    if not a or not b:
        return 0.0
    words_a, words_b = set(a.split()), set(b.split())
    shared = 2 * len(words_a & words_b) / (len(words_a) + len(words_b))
    return max(SequenceMatcher(None, a, b).ratio(), shared)

def build_query(name, title=None, artist=None):
    """Returns (search text, normalized artist, normalized title) for a file.

    An existing title tag is trusted over the file name; otherwise "Artist - Title" names are split.
    """
    if title:
        return f"{artist} {title}" if artist else title, normalize(artist), normalize(title)

    cleaned = clean_name(name)
    if not artist and ' - ' in cleaned:
        artist, title = cleaned.split(' - ', 1)
        return cleaned, normalize(artist), normalize(title)
    return f"{artist} {cleaned}" if artist else cleaned, normalize(artist), normalize(cleaned)

def score_track(track, artist, title):
    """0..1: how well a SoundCloud result matches the wanted (normalized) artist and title."""
    sc_title = normalize(track.get('title'))
    sc_uploader = normalize(track.get('uploader'))
    full = f"{artist} {title}".strip()

    score = max(similarity(full, sc_title), similarity(full, f"{sc_uploader} {sc_title}"))
    if artist:
        # Title alone matches: full marks only if the artist shows up too
        artist_found = artist in sc_uploader or artist in sc_title
        score = max(score, similarity(title, sc_title) * (1.0 if artist_found else 0.8))
    return score

def search_soundcloud(query, artist, title):
    """Searches SoundCloud and returns the metadata of the closest result.

    Returns None when nothing was found, or {'score': ...} alone when no result was close enough.
    """
    with yt_dlp.YoutubeDL({**YDL_OPTS, 'extract_flat': True}) as ydl:
        info = ydl.extract_info(f"scsearch{SEARCH_RESULTS}:{query}", download=False)

    entries = [e for e in (info or {}).get('entries') or [] if e]
    if not entries:
        return None

    # max() keeps the first of equal scores, i.e. SoundCloud's own ranking breaks ties
    best = max(entries, key=lambda e: score_track(e, artist, title))
    score = score_track(best, artist, title)
    if score < MIN_SCORE:
        return {'score': score}

    # Search results carry no genre or artwork: fetch the chosen track itself
    track = best
    url = best.get('webpage_url') or best.get('url')
    if url:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            track = ydl.extract_info(url, download=False) or best

    # Titles often already start with the artist
    match = best.get('title') or ''
    if best.get('uploader') and normalize(best['uploader']) not in normalize(match):
        match = f"{best['uploader']} - {match}"

    thumbnails = track.get('thumbnails') or []
    return {
        'score': score,
        'match': match,
        'artist': track.get('uploader') or best.get('uploader'),
        'genre': track.get('genre'),
        'artwork_url': track.get('thumbnail') or (thumbnails[-1].get('url') if thumbnails else None)
    }

def tag_mp3(filepath, name=None):
    """Fills in missing Artist, Genre and Artwork on one MP3 from SoundCloud.

    name: track name to search for when the file has no title tag (default: the file name).

    Returns {'code': 'already' | 'no_results' | 'no_match' | 'added' | 'nothing', 'added': [[field, value], ...]}
    where field is 'artist', 'genre' or 'artwork'; 'match' names the SoundCloud track used.
    Raises on search/network/file errors.
    """
    if name is None:
        name = os.path.splitext(os.path.basename(filepath))[0]

    # Load MP3 file and initialize ID3 tags if missing
    audio = MP3(filepath, ID3=ID3)
    try:
        audio.add_tags()
    except error:
        pass # Tags already exist

    def text_of(frame_id):
        return str(audio.tags[frame_id]).strip() if frame_id in audio.tags else ''

    # Check existing metadata
    has_artist = text_of('TPE1') != ''
    has_genre = text_of('TCON') != ''
    has_artwork = any(tag.startswith('APIC') for tag in audio.tags.keys())

    if has_artist and has_genre and has_artwork:
        return {'code': 'already', 'added': []}

    # If anything is missing, scrape SoundCloud
    sc_data = search_soundcloud(*build_query(name, text_of('TIT2'), text_of('TPE1')))

    if not sc_data:
        return {'code': 'no_results', 'added': []}
    if 'match' not in sc_data:
        return {'code': 'no_match', 'added': []}

    added = []

    # 1. Update Artist
    if not has_artist and sc_data.get('artist'):
        audio.tags.add(TPE1(encoding=3, text=sc_data['artist']))
        added.append(['artist', sc_data['artist']])

    # 2. Update Genre
    if not has_genre and sc_data.get('genre'):
        audio.tags.add(TCON(encoding=3, text=sc_data['genre']))
        added.append(['genre', sc_data['genre']])

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
            added.append(['artwork', None])

    # Save only if changes were made
    if added:
        audio.save()
        return {'code': 'added', 'added': added, 'match': sc_data['match']}

    return {'code': 'nothing', 'added': [], 'match': sc_data['match']}

def describe_tag_result(result):
    """English one-line summary of a tag_mp3() result."""
    if result['code'] == 'added':
        return "added " + ", ".join(
            field.capitalize() + (f" ({value})" if value else "") for field, value in result['added']
        ) + f" from \"{result['match']}\""
    return {
        'already': "already has Artist, Genre and Artwork",
        'no_results': "no results found on SoundCloud",
        'no_match': "no SoundCloud result was close enough to the track name",
        'nothing': "no missing data could be found",
    }[result['code']]

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
            print(f"  [*] {describe_tag_result(tag_mp3(os.path.join(folder_path, filename)))}")
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
