# Convertisseur MP3

Une petite application Windows et macOS qui convertit les fichiers audio (FLAC, WAV, M4A, OGG, WMA…) en MP3 320 kbps, et peut compléter l'artiste, le genre et la pochette manquants depuis SoundCloud. Elle tourne sur votre propre machine, dans sa propre fenêtre ; vos fichiers ne sont envoyés sur aucun serveur.

## Téléchargement

1. Rendez-vous sur la [dernière version](https://github.com/MatveiMKS/FlaccToMp3/releases/latest).
2. Téléchargez le fichier correspondant à votre ordinateur :
   - Windows : `MP3Converter.exe`
   - Mac Apple Silicon (M1 et suivants) : `MP3Converter-macOS-arm64.zip`
   - Mac Intel : `MP3Converter-macOS-x86_64.zip`

Il n'y a rien à installer. FFmpeg est inclus dans l'application. Sur Mac, décompressez le `.zip` (double-clic) pour obtenir l'application `MP3Converter`.

### Avertissement de sécurité

Le `.exe` n'est pas signé numériquement : un certificat de signature est payant, et ce projet n'en a pas. Windows et votre navigateur ne peuvent donc pas vérifier son éditeur et le signaleront comme potentiellement dangereux :

- Le navigateur peut bloquer le téléchargement ou afficher « Ce fichier n'est pas couramment téléchargé ». Choisissez **Conserver** (parfois sous **…** ou **Afficher plus**).
- Au premier lancement, Windows SmartScreen affiche « Windows a protégé votre ordinateur ». Cliquez sur **Informations complémentaires**, puis sur **Exécuter quand même**.
- Certains antivirus peuvent aussi signaler le fichier, comme ils le font souvent pour les programmes Python empaquetés avec PyInstaller.

Il en va de même sur Mac, où l'application n'est ni signée ni notariée par Apple : au premier lancement, macOS refuse de l'ouvrir. Ouvrez **Réglages Système > Confidentialité et sécurité**, descendez jusqu'au message concernant `MP3Converter` et cliquez sur **Ouvrir quand même**. Sur macOS 14 et antérieurs, un clic droit sur l'application puis **Ouvrir** suffit.

Si vous préférez ne pas lancer un exécutable non signé, le code source est dans ce dépôt et vous pouvez [lancer l'application depuis les sources](#lancer-depuis-les-sources). Les fichiers de chaque version sont générés par GitHub Actions à partir de ce même code.

## Utilisation

1. Double-cliquez sur `MP3Converter.exe` (ou sur l'application `MP3Converter` sur Mac ; au premier lancement, voir l'[avertissement de sécurité](#avertissement-de-sécurité)). Après quelques secondes, la fenêtre de l'application s'ouvre.
2. Choisissez une **Source** : cliquez sur **Dossier** pour sélectionner un dossier de fichiers audio, ou sur **Fichier** pour sélectionner une archive ZIP ou un seul fichier audio. Vous pouvez aussi coller un chemin, ou glisser-déposer le dossier ou le fichier dans la fenêtre.
3. Choisissez un dossier de **Destination**, ou activez **Créer un nouveau dossier** (voir ci-dessous).
4. Cliquez sur **Convertir**. La progression et un journal fichier par fichier s'affichent sous le formulaire. **Annuler** arrête la conversion en cours ; les fichiers déjà convertis sont conservés.
5. À la fin, un récapitulatif indique le nombre de fichiers convertis, en échec et tagués. **Ouvrir le dossier** affiche les MP3 dans l'Explorateur (le Finder sur Mac), et **Réessayer les échecs** relance uniquement les fichiers qui ont échoué.
6. Pour quitter, fermez la fenêtre.

L'onglet **Taguer seulement** complète les tags de MP3 existants (un dossier ou un seul fichier) sans rien convertir.

### Options

- **Créer un nouveau dossier** : enregistre les MP3 dans un nouveau dossier portant le nom de la source suivi de `_mp3` (`Album` ou `Album.zip` devient `Album_mp3`). Il est créé dans la destination, ou à côté de la source si la destination est laissée vide.
- **Taguer depuis SoundCloud** : recherche chaque morceau sur SoundCloud et complète l'artiste, le genre et la pochette lorsqu'ils sont absents. Les tags existants ne sont jamais écrasés. La recherche utilise le titre déjà présent dans le fichier, ou à défaut le nom du fichier débarrassé des numéros de piste (`01 - `) et des mentions comme `[FREE DL]`. Les cinq premiers résultats sont comparés au morceau et le plus proche est retenu ; si aucun n'est assez proche, le fichier n'est pas tagué. Un titre très court ou très courant peut malgré tout recevoir les tags d'un autre morceau du même nom.

### Bon à savoir

- Formats acceptés : FLAC, WAV, AIFF, M4A/ALAC, AAC, OGG, Opus, WMA, APE, WavPack, et les autres formats audio que FFmpeg sait lire.
- Avec un dossier comme source, seuls les fichiers placés directement dedans sont lus, pas ceux des sous-dossiers. Avec un ZIP, tous les fichiers audio sont pris, quelle que soit leur profondeur, et écrits dans un seul dossier de sortie.
- Quand deux fichiers donneraient le même MP3 (`CD1/01.flac` et `CD2/01.flac`, ou `titre.flac` et `titre.wav`), les suivants reçoivent un numéro : `01.mp3`, `01_2.mp3`, `01_3.mp3`.
- Quand une conversion échoue, le journal indique la raison donnée par FFmpeg.
- Les MP3 du même nom déjà présents dans le dossier de sortie sont écrasés.
- Les tags et la pochette déjà présents dans le fichier d'origine sont conservés dans le MP3.
- Les fichiers sont convertis en parallèle, un par cœur de processeur.
- Seuls les tags SoundCloud nécessitent une connexion Internet. Tout le reste fonctionne hors ligne.
- Sous Windows, la fenêtre utilise le composant Microsoft Edge WebView2, présent sur Windows 11 et sur les Windows 10 à jour ; sur Mac, elle utilise le moteur de Safari. Si la fenêtre ne peut pas être créée, l'application s'ouvre dans votre navigateur, avec une petite boîte de dialogue pour quitter.
- Les boutons **FR** / **EN** en haut à droite changent la langue de l'interface (français par défaut). Le bouton soleil/lune bascule entre le mode clair et le mode sombre. Les deux choix sont mémorisés.

## Lancer depuis les sources

Nécessite Python 3.12 et [FFmpeg](https://ffmpeg.org/download.html) dans votre PATH.

```bash
pip install -r requirements.txt
python app.py
```

L'application s'ouvre dans sa fenêtre. Pour la servir plutôt dans un navigateur, avec le rechargement automatique de Flask (le glisser-déposer n'y fonctionne pas) :

```bash
python app.py --browser
```

Ouvrez ensuite http://127.0.0.1:5000 (http://127.0.0.1:5050 sur Mac, où le port 5000 est pris par le récepteur AirPlay).

`tagger.py` peut aussi être lancé seul pour taguer un dossier de MP3 existants :

```bash
python tagger.py
```

## Générer l'application

Les versions sont générées par GitHub Actions ([.github/workflows/release.yml](.github/workflows/release.yml)). Pousser un tag commençant par `v` génère le `.exe` Windows et les deux applications Mac, puis les publie dans une nouvelle version :

```bash
git tag v1.0.1
git push origin v1.0.1
```

Pour générer le `.exe` en local, placez un `ffmpeg.exe` dans le dossier du projet et lancez :

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --icon static/icon.ico --name MP3Converter --add-data "templates;templates" --add-data "static;static" --add-binary "ffmpeg.exe;." app.py
```

Le résultat est `dist/MP3Converter.exe`.

Sur un Mac, placez un binaire `ffmpeg` statique dans le dossier du projet et lancez :

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --icon static/icon.icns --name MP3Converter --osx-bundle-identifier com.mp3converter.app --add-data "templates:templates" --add-data "static:static" --add-binary "ffmpeg:." app.py
```

Le résultat est `dist/MP3Converter.app`. PyInstaller ne sait pas compiler pour un autre système : l'application Mac ne peut être générée que sur un Mac, et pour l'architecture (Apple Silicon ou Intel) de ce Mac.

## Crédits

La conversion utilise [FFmpeg](https://ffmpeg.org), inclus dans l'application sous licence GPL. Les recherches SoundCloud utilisent [yt-dlp](https://github.com/yt-dlp/yt-dlp) ; les tags sont écrits avec [mutagen](https://mutagen.readthedocs.io). La fenêtre est fournie par [pywebview](https://pywebview.flowrl.com) et la mise en forme par [Tailwind CSS](https://tailwindcss.com), dont une copie est incluse dans `static/`.
