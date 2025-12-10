"""
ChronoReply - Discord Selfbot Scheduler
---------------------------------------

FITUR UTAMA:
- /kirim <waktu> <channel> <pesan>
- /ulang harian <HH:MM> <channel> <pesan>
- /ulang mingguan <hari> <HH:MM> <channel> <pesan>
- /daftar → menampilkan seluruh jadwal + isi pesan
- /hapus <ID> → menghapus jadwal
- /tz → menampilkan timezone default

Kode ini sudah dilengkapi dokumentasi lengkap, komentar profesional,
dan seluruh perbaikan struktural agar berjalan stabil.
"""

import json
import asyncio
import os
import uuid
from datetime import datetime, timedelta
import pytz
from typing import Union
import discord
from discord.ext import commands

# >>> CONFIG IMPORT <<<
from config import USER_TOKEN, PREFIX, DEFAULT_TZ, TASK_FILE


# =====================================================================
#                          TIME PARSER FUNCTION
# =====================================================================
def parse_time_input(time_input: str, default_tz: pytz.timezone) -> Union[datetime, None]:
    """
    Mengubah input waktu fleksibel menjadi datetime aware UTC.

    Format yang didukung:
      - HH:MM
      - DD-MM HH:MM
      - <hari> HH:MM  (senin–minggu)
    """
    now_local = datetime.now(default_tz)

    # Helper untuk perhitungan hari berikutnya
    def get_next_day(target_day_name, hour, minute):
        days_of_week = ['senin', 'selasa', 'rabu', 'kamis', 'jumat', 'sabtu', 'minggu']
        day_map = {name: i for i, name in enumerate(days_of_week)}

        target_weekday = day_map.get(target_day_name)
        if target_weekday is None:
            return None

        current_weekday = now_local.weekday()
        days_ahead = target_weekday - current_weekday
        if days_ahead < 0:
            days_ahead += 7

        next_dt = now_local + timedelta(days=days_ahead)
        target_dt = next_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Jika hari sama & jam sudah lewat → minggu depan
        if days_ahead == 0 and target_dt <= now_local:
            target_dt += timedelta(days=7)

        return target_dt

    parts = time_input.split()

    try:
        # Case 1: HH:MM
        if len(parts) == 1 and ":" in parts[0]:
            hour, minute = map(int, parts[0].split(":"))
            dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now_local:
                dt += timedelta(days=1)
            return dt.astimezone(pytz.utc)

        # Case 2: <hari> HH:MM
        if len(parts) == 2:
            day = parts[0].lower()
            if day in ['senin', 'selasa', 'rabu', 'kamis', 'jumat', 'sabtu', 'minggu']:
                hour, minute = map(int, parts[1].split(":"))
                dt = get_next_day(day, hour, minute)
                return dt.astimezone(pytz.utc) if dt else None

        # Case 3: DD-MM HH:MM
        if len(parts) == 2 and "-" in parts[0] and ":" in parts[1]:
            day, month = map(int, parts[0].split("-"))
            hour, minute = map(int, parts[1].split(":"))
            year = now_local.year

            dt = now_local.replace(day=day, month=month, year=year,
                                   hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now_local:
                dt = dt.replace(year=year + 1)
            return dt.astimezone(pytz.utc)

    except:
        return None

    return None


# =====================================================================
#                        CLASS: MAIN SELF-BOT
# =====================================================================
class ChronoReply(commands.Bot):
    """
    Bot utama, menangani:
    - pemuatan dan penyimpanan jadwal
    - scheduler loop
    - override command parser agar tidak error
    """

    def __init__(self):
        super().__init__(command_prefix=PREFIX, self_bot=True)

        self.default_tz = pytz.timezone(DEFAULT_TZ)
        self.tasks = self._load_tasks()

    # >>> IMPORTANT FIX <<<
    async def process_commands(self, message):
        """
        Override untuk mencegah CommandNotFound dari
        command parser bawaan discord.ext.commands.
        """
        return

    def _load_tasks(self):
        """Memuat task dari file JSON & mengubah waktu menjadi datetime."""
        if not os.path.exists(TASK_FILE):
            return []

        with open(TASK_FILE, "r") as f:
            try:
                data = json.load(f)
                for task in data:
                    if task.get("schedule_time_utc"):
                        try:
                            task["schedule_time_utc"] = datetime.strptime(
                                task["schedule_time_utc"], "%Y%m%d%H%M"
                            ).replace(tzinfo=pytz.utc)
                        except:
                            task["schedule_time_utc"] = None
                return [t for t in data if t["schedule_time_utc"]]
            except:
                return []

    def save_tasks(self):
        """Menyimpan task kembali ke JSON."""
        serialized = []
        for task in self.tasks:
            obj = task.copy()
            if obj.get("schedule_time_utc"):
                obj["schedule_time_utc"] = obj["schedule_time_utc"].astimezone(
                    pytz.utc
                ).strftime("%Y%m%d%H%M")
            serialized.append(obj)

        with open(TASK_FILE, "w") as f:
            json.dump(serialized, f, indent=4)

    async def on_ready(self):
        """Event ketika selfbot login."""
        print("----------------------------------")
        print(f"ChronoReply Selfbot Siap.")
        print(f"User: {self.user} (ID: {self.user.id})")
        print(f"Prefix: {PREFIX}")
        print(f"Timezone Default: {DEFAULT_TZ}")
        print("----------------------------------")

        asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self):
        """
        Scheduler background loop:
        - melakukan pengecekan setiap 5 detik
        - mengirim pesan jika waktu sudah tiba
        - menghitung jadwal berikutnya
        """
        await self.wait_until_ready()
        my_id = self.user.id

        while not self.is_closed():
            await asyncio.sleep(5)
            now = datetime.now(pytz.utc)

            pending_delete = []

            for task in self.tasks:
                if task["user_id"] != my_id:
                    continue

                schedule = task["schedule_time_utc"]
                if schedule and schedule <= now:
                    try:
                        channel = self.get_channel(task["channel_id"])
                        if channel:
                            await channel.send(task["content"])

                        if task["repeat_type"] == "once":
                            pending_delete.append(task["id"])
                        else:
                            task["schedule_time_utc"] = self._calculate_next_repeat(task)

                    except Exception as e:
                        print(f"[ERROR] Gagal kirim {task['id']}: {e}")

            if pending_delete:
                self.tasks = [t for t in self.tasks if t["id"] not in pending_delete]
                self.save_tasks()

    def _calculate_next_repeat(self, task):
        """Hitung jadwal berikutnya (harian/mingguan)."""
        tz = pytz.timezone(task["timezone"])
        now = datetime.now(tz)

        hour, minute = map(int, task["repeat_value"].split(":"))

        # Harian
        if task["repeat_type"] == "harian":
            next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_dt <= now:
                next_dt += timedelta(days=1)
            return next_dt.astimezone(pytz.utc)

        # Mingguan
        if task["repeat_type"] == "mingguan":
            day_name = task["repeat_day"].lower()
            days = ['senin', 'selasa', 'rabu', 'kamis', 'jumat', 'sabtu', 'minggu']

            if day_name not in days:
                return None

            target = days.index(day_name)
            current = now.weekday()
            diff = target - current
            if diff < 0:
                diff += 7

            next_dt = now + timedelta(days=diff)
            next_dt = next_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if diff == 0 and next_dt <= now:
                next_dt += timedelta(days=7)

            return next_dt.astimezone(pytz.utc)

        return None


# =====================================================================
#                          COMMANDS COG
# =====================================================================
class SchedulerCog(commands.Cog):
    """
    Tempat seluruh command berada:
    - /kirim
    - /ulang harian
    - /ulang mingguan
    - /daftar
    - /hapus
    - /tz
    """

    def __init__(self, bot):
        self.bot = bot

    # --------------------------------------------------------------
    # MAIN LISTENER (manual command dispatcher)
    # --------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        """Mendeteksi command manual karena selfbot tidak memakai parser normal."""
        if message.author != self.bot.user:
            return

        prefix = self.bot.command_prefix
        if not message.content.startswith(prefix):
            return

        parts = message.content[len(prefix):].split()
        if not parts:
            return

        command = parts[0].lower()
        args = parts[1:]

        ctx = await self.bot.get_context(message)

        # Routing command
        if command == "kirim":
            await self._kirim_once(ctx, *args)

        elif command == "ulang":
            await self._route_repeat(ctx, args)

        elif command == "daftar":
            await self._list_tasks(ctx)

        elif command == "hapus":
            await self._delete_task(ctx, *args)

        elif command == "tz":
            await self._show_timezone(ctx)

    # --------------------------------------------------------------
    # COMMAND: /kirim <waktu> <channel_id> <pesan>
    # --------------------------------------------------------------
    async def _kirim_once(self, ctx, *args):
        if len(args) < 3:
            return await ctx.send(f"❌ Format: {PREFIX}kirim <waktu> <channel_id> <pesan>")

        waktu, channel_id_str = args[0], args[1]
        pesan = " ".join(args[2:])

        try:
            channel_id = int(channel_id_str)
        except:
            return await ctx.send("❌ Channel ID tidak valid.")

        dt_utc = parse_time_input(waktu, self.bot.default_tz)
        if not dt_utc:
            return await ctx.send("❌ Format waktu tidak dikenali.")

        if dt_utc <= datetime.now(pytz.utc):
            return await ctx.send("❌ Waktu sudah lewat.")

        task = {
            "id": str(uuid.uuid4())[:8],
            "user_id": self.bot.user.id,
            "channel_id": channel_id,
            "content": pesan,
            "repeat_type": "once",
            "schedule_time_utc": dt_utc,
            "timezone": DEFAULT_TZ,
        }

        self.bot.tasks.append(task)
        self.bot.save_tasks()

        lokal = dt_utc.astimezone(self.bot.default_tz).strftime("%A, %d %B %Y %H:%M")
        await ctx.send(f"✅ Jadwal dibuat!\nID: `{task['id']}`\nKirim pada: **{lokal}**")

    # --------------------------------------------------------------
    # COMMAND ROUTER: /ulang
    # --------------------------------------------------------------
    async def _route_repeat(self, ctx, args):
        if len(args) < 1:
            return await ctx.send("❌ Format salah. Gunakan `ulang harian` atau `ulang mingguan`.")
        tipe = args[0].lower()
        await self._kirim_repeat(ctx, tipe, *args[1:])

    # --------------------------------------------------------------
    # COMMAND: /ulang harian / mingguan
    # --------------------------------------------------------------
    async def _kirim_repeat(self, ctx, tipe, *args):
        if tipe == "harian":
            if len(args) < 3:
                return await ctx.send(f"❌ Format: {PREFIX}ulang harian <HH:MM> <channel> <pesan>")

            time_str, channel_id_str = args[0], args[1]
            pesan = " ".join(args[2:])
            day = None

        elif tipe == "mingguan":
            if len(args) < 4:
                return await ctx.send(f"❌ Format: {PREFIX}ulang mingguan <hari> <HH:MM> <channel> <pesan>")

            day, time_str, channel_id_str = args[0].lower(), args[1], args[2]
            pesan = " ".join(args[3:])
        else:
            return await ctx.send("❌ Tipe ulang hanya `harian` atau `mingguan`.")

        try:
            channel_id = int(channel_id_str)
            hour, minute = map(int, time_str.split(":"))
        except:
            return await ctx.send("❌ Format waktu atau channel tidak valid.")

        # Hitung run pertama
        temp = {
            "repeat_type": tipe,
            "repeat_value": time_str,
            "repeat_day": day,
            "timezone": DEFAULT_TZ
        }

        first_run = self.bot._calculate_next_repeat(temp)
        if not first_run:
            return await ctx.send("❌ Gagal menghitung eksekusi pertama.")

        # Simpan task
        task = {
            "id": str(uuid.uuid4())[:8],
            "user_id": self.bot.user.id,
            "channel_id": channel_id,
            "content": pesan,
            "repeat_type": tipe,
            "repeat_value": time_str,
            "repeat_day": day,
            "schedule_time_utc": first_run,
            "timezone": DEFAULT_TZ,
        }

        self.bot.tasks.append(task)
        self.bot.save_tasks()

        lokal = first_run.astimezone(self.bot.default_tz).strftime("%A, %d %B %Y %H:%M")

        await ctx.send(
            f"✅ Jadwal repeat aktif!\n"
            f"ID: `{task['id']}`\n"
            f"Eksekusi pertama: **{lokal}**"
        )

    # --------------------------------------------------------------
    # COMMAND: /daftar
    # --------------------------------------------------------------
    async def _list_tasks(self, ctx):
        """Menampilkan seluruh jadwal + isi pesan."""

        tasks = [t for t in self.bot.tasks if t["user_id"] == self.bot.user.id]

        if not tasks:
            return await ctx.send("📝 Tidak ada jadwal aktif.")

        lines = ["**📝 Jadwal Aktif:**\n"]

        for t in tasks:
            next_run = t["schedule_time_utc"].astimezone(self.bot.default_tz).strftime("%d/%m %H:%M")
            channel = f"<#{t['channel_id']}>"

            # Preview isi pesan (max 50 char)
            preview = t["content"][:50] + ("..." if len(t["content"]) > 50 else "")

            lines.append(
                f"**ID:** `{t['id']}` | **{t['repeat_type']}** | ke {channel} | Next: {next_run}\n"
                f"• Pesan: {preview}\n"
            )

        await ctx.send("\n".join(lines))

    # --------------------------------------------------------------
    # COMMAND: /hapus <ID>
    # --------------------------------------------------------------
    async def _delete_task(self, ctx, task_id=None):
        if not task_id:
            return await ctx.send(f"❌ Gunakan: {PREFIX}hapus <ID>")

        before = len(self.bot.tasks)
        self.bot.tasks = [t for t in self.bot.tasks if t["id"] != task_id]

        if len(self.bot.tasks) < before:
            self.bot.save_tasks()
            return await ctx.send(f"✅ Jadwal `{task_id}` dihapus.")
        else:
            return await ctx.send("❌ ID tidak ditemukan.")

    # --------------------------------------------------------------
    # COMMAND: /tz
    # --------------------------------------------------------------
    async def _show_timezone(self, ctx):
        await ctx.send(f"⌛ Timezone default: `{self.bot.default_tz}`")


# =====================================================================
#                         START BOT
# =====================================================================
if __name__ == "__main__":

    # Pastikan file JSON ada
    if not os.path.exists(TASK_FILE):
        with open(TASK_FILE, "w") as f:
            json.dump([], f)

    # Validasi token
    if USER_TOKEN == "PASTIKAN_TOKEN_ANDA_ADA_DI_SINI":
        print("!!! ERROR: Harap isi USER_TOKEN di config.py !!!")
        exit()

    bot = ChronoReply()

    async def start():
        """Start bot with async add_cog."""
        await bot.add_cog(SchedulerCog(bot))
        print(f"Mencoba menjalankan bot dengan prefix '{PREFIX}'...")
        await bot.start(USER_TOKEN)

    asyncio.run(start())
