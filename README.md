# Convertisseur MP3

Une petite application Windows qui convertit les fichiers FLAC et WAV en MP3 320 kbps, et peut compléter l'artiste, le genre et la pochette manquants depuis SoundCloud. Elle tourne sur votre propre machine et s'ouvre dans votre navigateur ; vos fichiers ne sont envoyés sur aucun serveur.

## Téléchargement

1. Rendez-vous sur la [dernière version](https://github.com/MatveiMKS/FlaccToMp3/releases/latest).
2. Téléchargez `MP3Converter.exe`.

Il n'y a rien à installer. FFmpeg est inclus dans le `.exe`.

## Utilisation

1. Double-cliquez sur `MP3Converter.exe`. Une fenêtre de console apparaît et, après quelques secondes, l'application s'ouvre dans votre navigateur à l'adresse http://127.0.0.1:5000.
2. Choisissez une **Source** : cliquez sur **Dossier** pour sélectionner un dossier de fichiers FLAC/WAV, ou sur **ZIP** pour sélectionner une archive ZIP. Vous pouvez aussi coller un chemin.
3. Choisissez un dossier de **Destination**, ou activez **Créer un nouveau dossier** (voir ci-dessous).
4. Cliquez sur **Convertir**. La progression et un journal fichier par fichier s'affichent sous le formulaire.
5. Pour quitter, fermez la fenêtre de console.

Windows peut afficher un avertissement SmartScreen au premier lancement, car l'application n'est pas signée. Cliquez sur **Informations complémentaires**, puis sur **Exécuter quand même**.

### Options

- **Créer un nouveau dossier** : enregistre les MP3 dans un nouveau dossier portant le nom de la source suivi de `_mp3` (`Album` ou `Album.zip` devient `Album_mp3`). Il est créé dans la destination, ou à côté de la source si la destination est laissée vide.
- **Taguer depuis SoundCloud** : recherche chaque nom de fichier sur SoundCloud et complète l'artiste, le genre et la pochette lorsqu'ils sont absents. Les tags existants ne sont jamais écrasés. Le premier résultat de recherche est utilisé : un fichier au nom vague peut donc recevoir de mauvais tags.

### Bon à savoir

- Avec un dossier comme source, seuls les fichiers placés directement dedans sont lus, pas ceux des sous-dossiers. Avec un ZIP, tous les fichiers FLAC/WAV sont pris, quelle que soit leur profondeur, et écrits dans un seul dossier de sortie.
- Les MP3 du même nom déjà présents dans le dossier de sortie sont écrasés.
- Les tags et la pochette déjà présents dans un fichier FLAC sont conservés dans le MP3.
- Les fichiers sont convertis en parallèle, un par cœur de processeur.
- Une connexion Internet est nécessaire pour la mise en forme de la page et pour les tags SoundCloud. La conversion elle-même fonctionne hors ligne.
- Les boutons **FR** / **EN** en haut à droite changent la langue de l'interface (français par défaut). Le bouton soleil/lune bascule entre le mode clair et le mode sombre. Les deux choix sont mémorisés.

## Lancer depuis les sources

Nécessite Python 3.12 et [FFmpeg](https://ffmpeg.org/download.html) dans votre PATH.

```bash
pip install -r requirements.txt
python app.py
```

Ouvrez ensuite http://127.0.0.1:5000.

`tagger.py` peut aussi être lancé seul pour taguer un dossier de MP3 existants :

```bash
python tagger.py
```

## Générer le exe

Les versions sont générées par GitHub Actions ([.github/workflows/release.yml](.github/workflows/release.yml)). Pousser un tag commençant par `v` génère le `.exe` et le publie dans une nouvelle version :

```bash
git tag v1.0.1
git push origin v1.0.1
```

Pour le générer en local, placez un `ffmpeg.exe` dans le dossier du projet et lancez :

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --name MP3Converter --add-data "templates;templates" --add-binary "ffmpeg.exe;." app.py
```

Le résultat est `dist/MP3Converter.exe`.

## Crédits

La conversion utilise [FFmpeg](https://ffmpeg.org), inclus dans le `.exe` sous licence GPL. Les recherches SoundCloud utilisent [yt-dlp](https://github.com/yt-dlp/yt-dlp) ; les tags sont écrits avec [mutagen](https://mutagen.readthedocs.io).
