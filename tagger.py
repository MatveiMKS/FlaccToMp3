import base64
import os
import re
import unicodedata
from difflib import SequenceMatcher

import requests
import yt_dlp
from mutagen import File as MutagenFile
from mutagen.flac import Picture
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4Cover
from mutagen.wave import WAVE
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TCON, TDRC, TRCK, TPOS, TBPM, APIC

# Files the SoundCloud tagger can write to: both keep their tags in ID3
TAGGABLE_EXTENSIONS = ('.mp3', '.wav')
# ID3v2.3, not mutagen's default 2.4: Serato shows no artwork on 2.4 tags, and Windows can't read them
ID3_VERSION = 3

# Tags copied into a converted WAV, by their name in mutagen's format-independent ("easy") interface
EASY_TO_ID3 = {
    'title': TIT2, 'artist': TPE1, 'album': TALB, 'albumartist': TPE2, 'genre': TCON,
    'date': TDRC, 'tracknumber': TRCK, 'discnumber': TPOS, 'bpm': TBPM
}

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

    # The artist credited on the track, when the uploader filled it in: the uploader is often a label or a repost channel.
    # yt-dlp swaps commas inside a name for full-width ones in 'artist', so join 'artists' instead
    artists = track.get('artists') or []
    artist = ', '.join(artists) if artists else track.get('artist')

    # The 500x500 artwork: the original upload can weigh several MB, all of it embedded in every file
    thumbnails = track.get('thumbnails') or []
    artwork = next((t.get('url') for t in thumbnails if t.get('id') == 't500x500'), None)
    return {
        'score': score,
        'match': match,
        'artist': artist or track.get('uploader') or best.get('uploader'),
        'genre': track.get('genre'),
        'artwork_url': artwork or track.get('thumbnail') or (thumbnails[-1].get('url') if thumbnails else None)
    }

def open_id3(filepath):
    """Opens an MP3 or WAV with an ID3 tag, creating an empty one if it has none."""
    audio = WAVE(filepath) if filepath.lower().endswith('.wav') else MP3(filepath, ID3=ID3)
    if audio.tags is None:
        audio.add_tags()
    return audio

def source_pictures(source):
    """(mime, picture type, data) of the cover art embedded in a FLAC, Ogg/Opus or MP4 file."""
    if getattr(source, 'pictures', None):
        return [(p.mime, p.type, p.data) for p in source.pictures]
    tags = source.tags
    if tags is None:
        return []
    if 'covr' in tags:
        return [('image/png' if c.imageformat == MP4Cover.FORMAT_PNG else 'image/jpeg', 3, bytes(c)) for c in tags['covr']]
    if 'metadata_block_picture' in tags:
        pictures = [Picture(base64.b64decode(value)) for value in tags['metadata_block_picture']]
        return [(p.mime, p.type, p.data) for p in pictures]
    return []

def copy_tags_to_wav(src, wav_path):
    """Copies the tags and cover art of any audio file into a WAV's ID3 chunk, which is what DJ software reads."""
    source = MutagenFile(src)
    if source is None or source.tags is None:
        return
    wav = open_id3(wav_path)

    if isinstance(source.tags, ID3):
        # WAV, AIFF, DSF...: already ID3, copy every frame as is
        for frame in source.tags.values():
            wav.tags.add(frame)
    else:
        easy = MutagenFile(src, easy=True)
        for key, frame in EASY_TO_ID3.items():
            try:
                values = easy.tags[key] if easy and easy.tags is not None else None
            except (KeyError, ValueError):
                values = None
            if values:
                wav.tags.add(frame(encoding=3, text=[str(v) for v in values]))
        for mime, kind, data in source_pictures(source):
            wav.tags.add(APIC(encoding=3, mime=mime, type=kind, desc='Cover' if kind == 3 else '', data=data))
    wav.save(v2_version=ID3_VERSION)

def split_name(name):
    """(artist, title) read from an "Artist - Title" file name; artist is '' when the name has no " - "."""
    cleaned = clean_name(name)
    if ' - ' in cleaned:
        artist, title = cleaned.split(' - ', 1)
        return artist.strip(), title.strip()
    return '', cleaned

def tag_file(filepath, name=None):
    """Fills in missing Title, Artist, Genre and Artwork on one MP3 or WAV.

    Title and artist come from an "Artist - Title" file name first, the rest from SoundCloud.
    name: track name to use when the file has no title tag (default: the file name).

    Returns {'code': 'already' | 'no_results' | 'no_match' | 'added' | 'nothing', 'added': [[field, value], ...]}
    where field is 'title', 'artist', 'genre' or 'artwork'; 'match' names the SoundCloud track used, if any.
    Raises on search/network/file errors.
    """
    if name is None:
        name = os.path.splitext(os.path.basename(filepath))[0]

    audio = open_id3(filepath)

    def text_of(frame_id):
        return str(audio.tags[frame_id]).strip() if frame_id in audio.tags else ''

    added = []
    title, artist = text_of('TIT2'), text_of('TPE1')

    # Without a title or artist tag, DJ software lists the track by its file name: read them from "Artist - Title"
    if not title or not artist:
        file_artist, file_title = split_name(name)
        if not title and file_title:
            title = file_title
            audio.tags.add(TIT2(encoding=3, text=title))
            added.append(['title', title])
        if not artist and file_artist:
            artist = file_artist
            audio.tags.add(TPE1(encoding=3, text=artist))
            added.append(['artist', artist])

    has_genre = text_of('TCON') != ''
    has_artwork = any(tag.startswith('APIC') for tag in audio.tags.keys())

    def finish(code, match=None):
        if added:
            audio.save(v2_version=ID3_VERSION)
            return {'code': 'added', 'added': added, 'match': match}
        result = {'code': code, 'added': []}
        if match:
            result['match'] = match
        return result

    if artist and has_genre and has_artwork:
        return finish('already')

    # If anything is missing, scrape SoundCloud
    sc_data = search_soundcloud(*build_query(name, title, artist))

    if not sc_data:
        return finish('no_results')
    if 'match' not in sc_data:
        return finish('no_match')

    # 1. Update Artist
    if not artist and sc_data.get('artist'):
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

    return finish('nothing', sc_data['match'])

def describe_tag_result(result):
    """English one-line summary of a tag_file() result."""
    if result['code'] == 'added':
        source = f"\"{result['match']}\"" if result.get('match') else "the file name"
        return "added " + ", ".join(
            field.capitalize() + (f" ({value})" if value else "") for field, value in result['added']
        ) + f" from {source}"
    return {
        'already': "already has Artist, Genre and Artwork",
        'no_results': "no results found on SoundCloud",
        'no_match': "no SoundCloud result was close enough to the track name",
        'nothing': "no missing data could be found",
    }[result['code']]

def process_folder(folder_path):
    """Iterates through MP3s and WAVs and updates missing metadata."""
    if not folder_path:
        print("No folder selected. Exiting.")
        return

    for filename in os.listdir(folder_path):
        if not filename.lower().endswith(TAGGABLE_EXTENSIONS):
            continue

        print(f"\nProcessing: {filename}")
        try:
            print(f"  [*] {describe_tag_result(tag_file(os.path.join(folder_path, filename)))}")
        except Exception as e:
            print(f"  [!] Failed: {e}")

if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw() # Hide the main tkinter window
    target_folder = filedialog.askdirectory(title="Select Folder with MP3s or WAVs")
    root.destroy()

    process_folder(target_folder)
    print("\nDone!")
