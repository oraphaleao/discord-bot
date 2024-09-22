"""Contains the discord commands cog for the music bot, MusicCog,
which contains the main logic for the music bot's behavior."""

import asyncio
import time
import traceback
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from typing import override

import discord
from discord.ext import commands
from spotipy import SpotifyException
from yt_dlp.utils import YoutubeDLError

from config import Config

from .audio_player import AudioPlayer
from .song_factory import SongFactory
from .spotify import SpotifyClientWrapper
from .stats import StatsFactory
from .usage_database import UsageDatabase
from .utils import (
    extract_discord_user_id,
    is_int,
    is_spotify_album_or_playlist,
    is_spotify_track,
    is_yt_playlist,
    is_yt_video,
)
from .ytdl_source import YtdlSourceFactory


class MusicCog(commands.Cog):
    """A custom discord commands cog for the music bot.

    Has definitions for each of the music bot's commands, listeners, and optional state.

    Attributes:
        config: A Config object representing the configuration of the music bot.
        bot: The commands.Bot object representing the music bot itself.
        executor: concurrent.futures.Executor object, used to execute yt-dlp and spotify calls that would otherwise
            block the asyncio event loop. Will be a ProcessPoolExecutor object if config.enable_multiprocessing
            is True, otherwise will be a ThreadPoolExecutor.
        ytdl_source_factory: YtdlSourceFactory object used to create and process YtdlSource objects
            with YouTube data retrieved from yt-dlp.
        spotify_client_wrapper: SpotifyClientWrapper object used to retrieve data from Spotify using spotipy.
        usage_db: UsageDatabase object representing the database tracking usage data for the music bot.
        song_factory: SongFactory object responsible for creating Song objects from YouTube and Spotify data.
        stats_factory: StatsFactory object responsibly for creating Stats objects, which are used
            to display usage statistics to users.
        audio_players: A dictionary mapping discord guild ids to AudioPlayer objects, used to store the audio players
            for each guild the bot is active on.
        default_reaction: The default reaction the bot will use to react to users' commands.
        reactions: A dictionary mapping command names to the reaction that the bot will use for that command.
    """

    def __init__(self, config: Config, bot: commands.Bot):
        self.config: Config = config
        self.bot: commands.Bot = bot

        self.executor: Executor = (
            ProcessPoolExecutor(max_workers=config.process_pool_workers)
            if config.enable_multiprocessing
            else ThreadPoolExecutor(max_workers=config.thread_pool_workers)
        )

        self.usage_db: UsageDatabase = (
            UsageDatabase(config) if config.enable_usage_database else None
        )
        self.ytdl_source_factory: YtdlSourceFactory = YtdlSourceFactory(
            config, self.executor
        )
        self.spotify_client_wrapper: SpotifyClientWrapper = SpotifyClientWrapper(
            config, self.executor
        )
        self.song_factory: SongFactory = SongFactory(
            config, self.ytdl_source_factory, self.spotify_client_wrapper
        )
        self.stats_factory: StatsFactory = StatsFactory(
            config, self.usage_db, self.ytdl_source_factory, self.spotify_client_wrapper
        )
        self.audio_players: dict[int, AudioPlayer] = dict()
        self.default_reaction: str = "✅"
        self.reactions: dict[str, str] = {
            "join": "👋",
            "leave": "👋",
            "pause": "⏯",
            "resume": "⏯",
            "stop": "⏹",
            "skip": "⏭",
            "play": "🎵",
            "loop": "🔁",
            "now": "✅",
            "queue": "✅",
            "shuffle": "🔀",
            "remove": "❌",
            "slap": "😱",
        }

    @override
    async def cog_load(self):
        if self.config.enable_usage_database:
            await self.usage_db.initialize()
        print("booting up")

    @override
    async def cog_unload(self):
        self.executor.shutdown(wait=False)
        tasks = [audio_player.leave() for audio_player in self.audio_players.values()]
        await asyncio.gather(*tasks)

    @override
    def cog_check(self, ctx: commands.Context):
        if not ctx.guild:
            raise commands.NoPrivateMessage(
                "Uai, ta doido zé? Pode sussurrar no meu zovido assim não, só lá no discord do Fresco."
            )
        return True

    @override
    async def cog_before_invoke(self, ctx):
        print("Starting the cog")
        ctx.audio_player = self.get_audio_player(ctx.guild.id)
        print(ctx.audio_player)
        return

    @override
    async def cog_after_invoke(self, ctx):
        print("Stopping the cog")
        try:
            if (
                ctx.audio_player
                and ctx.audio_player.audio_player_task
                and ctx.audio_player.audio_player_task.done()
            ):
                print(
                    f"Exception in audio player: {ctx.audio_player.audio_player_task.exception()}"
                )
        except asyncio.CancelledError as e:
            print(type(e))
            print("Task was cancelled: ", str(e))

        await ctx.message.add_reaction(
            self.reactions.get(ctx.command.name, self.default_reaction)
        )

    def get_audio_player(self, guild_id: int) -> AudioPlayer:
        """Gets the audio player associated with the given guild, or creates it.

        Args:
            guild_id: The integer id for the guild that we're getting the audio player for.

        Returns:
            The AudioPlayer object for that guild, freshly created if it didn't already exist.
        """
        audio_player = self.audio_players.get(guild_id)
        if audio_player:
            print("Retrieved audio player")
        if not audio_player:
            audio_player = AudioPlayer(self.config, self.usage_db, self.bot)
            self.audio_players[guild_id] = audio_player
            print(f"Stored audio player: {audio_player}")
        return audio_player

    @override
    async def cog_command_error(self, ctx: commands.Context, error: Exception):
        print(type(error))
        if isinstance(error, commands.errors.CommandNotFound):
            await ctx.send(
                'Num sei desse comando não. Manda ai "!!help" pra ver a lista de comando que eu sei zé.'
            )
        elif isinstance(error, commands.UserInputError):
            await ctx.send(f"Bad user input received: {str(error)}")
        elif isinstance(error, YoutubeDLError):
            await ctx.send(
                f"YoutubeDL threw an error with the following message: {str(error)}"
            )
        elif isinstance(error, 'SpotifyException'):
            await ctx.send(
                "Cê tem certeza que vai continuar mandando link do Spotify errado pra mim?"
            )
        else:
            await ctx.send(
                f"An unexpected error occurred in {ctx.command.name}: {str(error)}"
            )

        traceback.print_exception(error)

        # Comando para alterar o status do bot
    @commands.is_owner()  # Verifica se o dono está executando o comando
    @commands.command(name="mudarestado")
    async def mudar_status(self, ctx, tipo: str, *, mensagem: str):
        tipo = tipo.lower()  # Certificar que tipo é uma string e em minúsculas

        # Definir o status conforme o tipo fornecido
        if tipo == "jogando":
            activity = discord.Game(name=mensagem)
        elif tipo == "ouvindo":
            activity = discord.Activity(type=discord.ActivityType.listening, name=mensagem)
        elif tipo == "assistindo":
            activity = discord.Activity(type=discord.ActivityType.watching, name=mensagem)
        else:
            await ctx.send("Tipo inválido! Use: jogando, ouvindo ou assistindo.")
            return

        # Alterar o status do bot
        await self.bot.change_presence(status=discord.Status.online, activity=activity)
        await ctx.send(f"Status alterado para {tipo} {mensagem}")

    # Tratar o erro se uma pessoa que não é o dono tentar executar o comando
    @mudar_status.error
    async def mudar_status_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("Apenas o dono do bot pode usar esse comando!")
        else:
            # Lidar com outros tipos de erro
            await ctx.send(f"Ocorreu um erro: {error}")

    @commands.command(name="clear")
    async def clear(self, ctx: commands.Context):
        """Limpa a fila de espera. Lembrando que não afeta a música que está tocando."""

        if not ctx.audio_player.song_queue:
            await ctx.send("Não existe nenhuma música na fila.")
        else:
            ctx.audio_player.clear_song_queue()
            await ctx.send("Fila está limpa agora.")

    @override
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceChannel,
        after: discord.VoiceChannel,
    ):
        if member.id != self.bot.user.id and after.channel is None:
            audio_player = self.get_audio_player(before.channel.guild.id)
            if (
                audio_player.voice_client
                and before.channel.id == audio_player.voice_client.channel.id
                and len(before.channel.voice_states) <= 1
            ):
                await audio_player.leave()

    @commands.command(name="join", aliases=["summon"], invoke_without_subcommand=True)
    async def join(self, ctx: commands.Context):
        """Se junta ao canal de voz."""
        destination = ctx.author.voice.channel
        if ctx.audio_player.voice_client:
            await ctx.audio_player.voice_client.move_to(destination)
        else:
            ctx.audio_player.voice_client = await destination.connect()

        if (
            not ctx.audio_player.audio_player_task
            or ctx.audio_player.audio_player_task.done()
        ):
            ctx.audio_player.start_audio_player()

        print(ctx.audio_player.voice_client)

    @commands.command(name="leave", aliases=["disconnect", "die"])
    async def leave(self, ctx: commands.Context):
        """Faz ele parar de tocar e desconecta ele do chat de voz."""

        if not await ctx.audio_player.leave():
            await ctx.send("Rapaz, ta doidé? To conectado em nada não.")
        else:
            del self.audio_players[ctx.guild.id]

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        """Pausa a música atual que está tocando."""

        if not ctx.audio_player.is_currently_playing:
            await ctx.send("Xii, tem música tocando pra eu pausar não.")
        elif not ctx.audio_player.pause():
            await ctx.send("Ta pirando né? Música já ta pausada.")
        else:
            await ctx.send(f"Pausei pra você {ctx.audio_player.current_song}.")

    @commands.command(name="resume", aliases=["unpause", "continue"])
    async def resume(self, ctx: commands.Context):
        """Faz a música voltar a tocar."""

        if not ctx.audio_player.is_currently_playing:
            await ctx.send("Cê ta doido, doido? Tem música tocando não.")
        elif not ctx.audio_player.resume():
            await ctx.send("Ou cê pirou ou eu pirei, porque a música já ta tocando.")
        else:
            await ctx.send(f"Escutou ai? Escutei aqui agora a {ctx.audio_player.current_song}.")

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        """Para completamente de tocar a música.
        Para a música atual e ainda limpa a fila atual."""

        if not await ctx.audio_player.stop():
            await ctx.send("Cê ta doido, doido? Tem música tocando não.")
        else:
            await ctx.send("Parou porque? Porque parou? PAREI PORQUE QUIS (mentira, você mandou).")

    @commands.command(name="back")
    async def back(self, ctx: commands.Context):
        """Volta na música anterior."""

        if not ctx.audio_player.prev_songs:
            return await ctx.send("Tem música anterior aqui nas minhas memória RAM não.")

        if not await ctx.audio_player.skip(back=True):
            return await ctx.send("Cê ta doido, doido? Tem música tocando não.")

    @commands.command(name="skip", aliases=["s", "pular"])
    async def skip(self, ctx: commands.Context):
        """Pula a música atual."""

        if not await ctx.audio_player.skip():
            return await ctx.send("Cê ta doido, doido? Tem música tocando não.")

    @commands.command(name="status")
    async def status(self, ctx: commands.Context):
        """Mostra a música atual e a fila, caso exista."""

        if ctx.audio_player.is_queue_looping:
            await ctx.send("Ao infinito e além! Ta no loop essa fila, tá?.")
        else:
            await ctx.send("Tem nada em loop por aqui.")
        await ctx.invoke(self.now)
        await ctx.invoke(self.queue)

    @commands.command(name="now", aliases=["current", "playing"])
    async def now(self, ctx: commands.Context):
        """Mostra a música atual, caso esteja tocando."""

        if not ctx.audio_player.is_currently_playing:
            return await ctx.send("Cê ta doido, doido? Tem música tocando não.")

        await ctx.send(embed=ctx.audio_player.current_song.create_embed())

    @commands.command(name="queue", aliases=["showqueue", "fila"])
    async def queue(self, ctx: commands.Context, *, page: int = 1):
        """Mostra as músicas na fila.
        Você também pode indicar a página que quer ver."""
        if not ctx.audio_player.song_queue:
            return await ctx.send("Opa, é lotérica? Fila ta vazia uai.")

        await ctx.send(embed=ctx.audio_player.get_song_queue_embed(page))

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context):
        """Randomiza a fila."""

        if not ctx.audio_player or not ctx.audio_player.song_queue:
            return await ctx.send("Opa, é lotérica? Fila ta vazia uai.")

        ctx.audio_player.shuffle_song_queue()

    @commands.command(name="stats")
    async def stats(self, ctx: commands.Context, *args):
        """Quer status de uma música ou usuário? Toma."""

        if not self.config.enable_usage_database:
            await ctx.send("Status não ta habilitado.")
            return

        create_stats_kwargs = dict()

        index = 0
        if args:
            user_id = extract_discord_user_id(args[index])
            if user_id:
                create_stats_kwargs["user"] = ctx.guild.get_member(user_id)
                index += 1

        if len(args) > index:
            if is_spotify_album_or_playlist(args[index]):
                return await ctx.send(
                    "Você só pode pedir status de uma música específica do Spotify, não de um álbum/playlist."
                )
            elif is_yt_playlist(args[index]) and not is_yt_video(args[index]):
                return await ctx.send(
                    "Você só pode pedir status de uma música específica do Youtube, não de uma playlist."
                )
            elif is_spotify_track(args[index]):
                create_stats_kwargs["spotify_args"] = args[index]
                create_stats_kwargs["is_yt_search"] = True
            elif is_yt_video(args[index]):
                create_stats_kwargs["ytdl_args"] = args[index]
                create_stats_kwargs["is_yt_search"] = False
            else:
                create_stats_kwargs["ytdl_args"] = " ".join(args[index:])
                create_stats_kwargs["is_yt_search"] = True

        print("create_stats_kwargs: ", create_stats_kwargs)
        async with ctx.typing():
            stats = await self.stats_factory.create_stats(ctx, **create_stats_kwargs)
            await ctx.send(embed=stats.create_main_embed())
            if self.config.enable_stats_usage_graph and stats.figure_filename:
                figure_file, embed = stats.create_figure_embed()
                await ctx.send(embed=embed, file=figure_file)
                await ctx.send(file=figure_file)

    @commands.command(name="remove")
    async def remove(self, ctx: commands.Context, *args):
        """Removes a song from the queue given an index or search query."""

        if not ctx.audio_player.song_queue:
            await ctx.send("Empty queue.")
            return

        if is_int(args[0]):  # Index
            index = int(args[0])
            removed_song = ctx.audio_player.remove_from_song_queue(index=index - 1)
            if not removed_song:
                await ctx.send(f"{index} is not a valid index.")
                return
        else:  # YouTube search query
            ytdl_args = " ".join(args)
            yt_playlist = await self.song_factory.create_yt_playlist(
                ytdl_args, is_yt_search=True
            )
            song_ids = {song.id for song in yt_playlist}
            print(f"song_ids: {song_ids}")
            removed_song = ctx.audio_player.remove_from_song_queue(song_ids=song_ids)
            if not removed_song:
                await ctx.send(
                    f'No song was found in the queue that matched the arguments "{ytdl_args}."'
                )
                return

        await ctx.send(f"{removed_song} was successfully removed from the queue.")

    @commands.command(name="slap", aliases=["punch", "tapa"])
    async def slap(self, ctx: commands.Context):
        """Tapinha no bot."""
        await ctx.send("UEPA!  😱\nDesmerela! Bate em mim não debônio")

    @commands.command(name="loop", aliases=["repeat"])
    async def loop(self, ctx: commands.Context):
        """Deixa a fila em Loop.
        Execute este comando novamente para parar o loop.
        """

        ctx.audio_player.flip_is_queue_looping()
        if ctx.audio_player.is_queue_looping:
            await ctx.send("Ao infinito e além! Ta no loop essa fila, tá?.")
        else:
            await ctx.send("Agora a fila já não ta mais no loop.")

    async def _play(self, ctx: commands.Context, args: str, play_next: bool = False):
        """Helper function for the play and playnext commands.

        Houses the main logic for playing songs.

        Args:
            ctx: The discord command context in which a command is being invoked.
            args: A string containing the song (or playlist) to play. Can be a YouTube url, Spotify url,
                or YouTube search query.
            play_next: A boolean indicating whether or not to play the song (or playlist) next
                or after all the other songs.
        """

        if not args:
            raise commands.UserInputError(
                "No arguments provided. Please provide a url or search query."
            )

        if not ctx.audio_player.voice_client:
            print("joining voice channel")
            await ctx.invoke(self.join)
        async with ctx.typing():
            # Set command context of song factory
            self.song_factory.ctx = ctx

            song, playlist = None, None
            if is_yt_playlist(args):
                playlist = await self.song_factory.create_yt_playlist(args)
            elif is_spotify_album_or_playlist(args):
                playlist = await self.song_factory.create_spotify_collection(args)
            elif is_spotify_track(args):
                song = await self.song_factory.create_song_from_spotify_track(args)
            else:  # Must be youtube video url or search query
                is_yt_search = not is_yt_video(args)
                song = await self.song_factory.create_song_from_yt_video(
                    args, is_yt_search=is_yt_search
                )

            # Single song
            if song:
                if self.config.enable_usage_database:
                    start = time.time()
                    await self.usage_db.insert_data(song.create_song_request())
                    end = time.time()
                    print(f"Inserting song request took {end - start} seconds.")
                ctx.audio_player.add_to_song_queue(song, play_next=play_next)
                song_name = song.title if hasattr(song, 'title') else str(song)
                if play_next:
                    await ctx.send(f"Vamos tocar {song} na próxima.")
                else:
                    await ctx.send(f"Vou tocar agora (lá ele) {song}.")
                    # activity = discord.Activity(type=discord.ActivityType.listening, name=song_name)
                    # await self.bot.change_presence(status=discord.Status.online, activity=activity)
            else:  # Playlist
                await ctx.send(embed=playlist.create_embed())

                # Process and add to queue
                asyncio.get_running_loop().create_task(
                    self.song_factory.process_playlist(playlist)
                )
                for song in playlist:
                    await song.is_processed_event.wait()
                    if self.config.enable_usage_database:
                        start = time.time()
                        await self.usage_db.insert_data(song.create_song_request())
                        end = time.time()
                        print(f"Inserting song request took {end - start} seconds.")
                    ctx.audio_player.add_to_song_queue(song, play_next=play_next)

                await ctx.send(
                    f"Finished processing **{playlist.playlist_link_markdown}**. "
                    + "Use `-queue` to see the songs added to the queue."
                )

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, args: str):
        """Plays a song, or a collection of songs from an album or playlist.

        Plays a song (or songs) given a YouTube search query, YouTube video url, YouTube playlist url,
        or Spotify track, album, or playlist url or uri.
        If there are other songs in the queue, the song(s) will be added after them.
        """
        await self._play(ctx, args)

    @commands.command(name="playnext")
    async def playnext(self, ctx: commands.Context, *, args: str):
        """Plays a song, or a collection of songs from an album or playlist.

        Plays a song (or songs) given a YouTube search query, YouTube video url, YouTube playlist url,
        or Spotify track, album, or playlist url or uri.
        If there are other tracks in the queue, the song(s) will be prioritized and added before them.
        """
        await self._play(ctx, args, play_next=True)

    @join.before_invoke
    @play.before_invoke
    async def ensure_voice_connection(self, ctx: commands.Context):
        """Ensures the bot is connected to the requester's voice channel."""
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError("Rapaz, quer que eu conecte aonde? Se nem sei onde você tá... Conecta em um canal de voz criatura!")

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError("Me atrapalha não, que eu já to dando meu show em outro canal de voz.")
