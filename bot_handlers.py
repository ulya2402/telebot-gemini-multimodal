import logging
from telegram import Update, Message
from telegram.constants import ChatAction, ParseMode, ChatType
from telegram.ext import ContextTypes, CallbackContext
from telegram.error import BadRequest, RetryAfter
import gemini_client
from config import (
    GROUP_TRIGGER_COMMANDS,
    IMAGE_UNDERSTANDING_ENABLED,
    MAX_IMAGE_INPUT,
    MEDIA_GROUP_PROCESSING_DELAY,
    DEFAULT_PROMPT_FOR_IMAGE_IF_NO_CAPTION
)


logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.message.chat_id
    if gemini_client.reset_chat_history(chat_id): # Modifikasi untuk cek return value reset
        logger.info(f"Riwayat chat untuk {chat_id} direset karena perintah /start.")
    else:
        logger.info(f"Tidak ada riwayat chat aktif untuk {chat_id} untuk direset saat /start.")
    await update.message.reply_html(
        f"Halo {user.mention_html()}! aku adalah bot AI yang terhubung ke Gemini. ",
    )
    logger.info(f"User {user.id} ({user.first_name}) memulai bot di chat {chat_id}.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menerima pesan teks, mengirim ke Gemini, dan mencoba membalas."""
    message = update.message
    user_message = message.text
    user = update.effective_user
    chat_id = message.chat_id
    chat_type = message.chat.type
    message_id = message.message_id # Tambahkan ini untuk logging

    if not user_message:
        logger.debug(f"Pesan tanpa teks diterima dari {user.id} di chat {chat_id}. Diabaikan.")
        return

    logger.info(f"Menerima pesan (message_id: {message_id}) dari {user.id} ({user.first_name}) di chat {chat_id} (tipe: {chat_type}): \"{user_message}\"")

    should_respond = False
    actual_message_to_process = user_message
    trigger_command_used = None # Untuk digunakan di pesan bantuan jika prompt kosong

    if chat_type == ChatType.PRIVATE:
        should_respond = True
        logger.debug(f"Pesan di private chat {chat_id}. Bot akan merespon.")
    elif chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        logger.debug(f"Pesan di grup {chat_id}. Mengecek kondisi respon...")
        bot_id = context.bot.id

        if message.reply_to_message and message.reply_to_message.from_user.id == bot_id:
            should_respond = True
            actual_message_to_process = user_message
            logger.info(f"Pesan di grup {chat_id} adalah reply ke bot (ID: {bot_id}). Bot akan merespon dengan: \"{actual_message_to_process}\"")
        else:
            msg_lower = user_message.lower()
            for trigger_command_config in GROUP_TRIGGER_COMMANDS:
                trigger = trigger_command_config.lower()
                if msg_lower.startswith(trigger):
                    if len(msg_lower) == len(trigger):
                        should_respond = True
                        actual_message_to_process = ""
                        trigger_command_used = trigger_command_config
                        logger.info(f"Pesan di grup {chat_id} adalah trigger command '{trigger_command_config}' saja. Bot akan merespon.")
                        break
                    elif len(msg_lower) > len(trigger) and msg_lower[len(trigger)].isspace():
                        should_respond = True
                        actual_message_to_process = user_message[len(trigger):].strip()
                        trigger_command_used = trigger_command_config
                        logger.info(f"Pesan di grup {chat_id} menggunakan trigger command '{trigger_command_config}'. Teks diproses: \"{actual_message_to_process}\". Bot akan merespon.")
                        break

            if not should_respond:
                 logger.debug(f"Pesan di grup {chat_id} bukan reply ke bot dan tidak menggunakan trigger command yang valid. Bot tidak merespon.")

    if not should_respond:
        logger.debug(f"Kondisi respon tidak terpenuhi untuk pesan di chat {chat_id}. Bot tidak mengirim balasan.")
        return

    if not actual_message_to_process and chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        logger.info(f"Pesan proses kosong setelah trigger command di grup {chat_id}. Bot tidak mengirim ke Gemini.")
        if trigger_command_used: # Hanya kirim bantuan jika trigger command digunakan
             await message.reply_text(f"Mohon sertakan pertanyaan Anda setelah `{trigger_command_used}` atau periksa /help.", parse_mode=ParseMode.MARKDOWN)
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)


    text_parts = [actual_message_to_process] if actual_message_to_process else []


    gemini_reply = await gemini_client.generate_multimodal_response(
        chat_id=chat_id,
        prompt_parts=text_parts,
        text_prompt_for_history=actual_message_to_process if actual_message_to_process else None
    )

    if gemini_reply:
        try:
            await message.reply_text(gemini_reply, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Mengirim balasan Gemini ke chat {chat_id} (reply ke message_id: {message.message_id})")
        except BadRequest as e:
            if "can't parse entities" in str(e).lower():
                logger.warning(f"Gagal mengirim sebagai Markdown ke chat {chat_id}: {e}. Mencoba plain text.")
                try:
                    await message.reply_text(gemini_reply)
                    logger.info(f"Mengirim balasan Gemini (Plain Text Fallback) ke chat {chat_id} (reply ke message_id: {message.message_id})")
                except Exception as fallback_e:
                    logger.error(f"Gagal mengirim fallback plain text ke chat {chat_id}: {fallback_e}")
                    await message.reply_text("Maaf, saya kesulitan mengirim balasan. Silakan coba lagi.")
            else:
                logger.error(f"Error BadRequest (bukan parsing) saat mengirim balasan ke chat {chat_id}: {e}")
                await message.reply_text("Maaf, terjadi kesalahan saat mengirim balasan.")
        except Exception as e:
            logger.error(f"Error tak terduga saat mengirim balasan ke chat {chat_id}: {e}", exc_info=True)
            await message.reply_text("Maaf, terjadi kesalahan tak terduga saat mengirim balasan.")
    else:
        await message.reply_text("Maaf, terjadi kesalahan internal saat memproses permintaan Anda.")
        logger.error(f"Gagal mendapatkan balasan valid dari gemini_client untuk chat {chat_id} untuk pesan: \"{actual_message_to_process}\"")


async def reset_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani perintah /reset."""
    chat_id = update.message.chat_id
    user = update.effective_user
    if gemini_client.reset_chat_history(chat_id): # Memanggil reset dari gemini_client
        await update.message.reply_text("Oke, saya telah melupakan percakapan kita sebelumnya di chat ini.")
        logger.info(f"User {user.id} ({user.first_name}) mereset riwayat di chat {chat_id}.")
    else:
        await update.message.reply_text("Gagal mereset riwayat atau memang belum ada percakapan.")
        logger.warning(f"User {user.id} ({user.first_name}) mencoba mereset riwayat di chat {chat_id}, operasi reset mengembalikan False.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Memberikan pesan bantuan dasar."""
    user = update.effective_user
    logger.info(f"User {user.id} ({user.first_name}) memanggil /help di chat {update.message.chat_id}.")

    trigger_commands_text_list = [f"`{cmd}`" for cmd in GROUP_TRIGGER_COMMANDS]
    trigger_commands_text = ", ".join(trigger_commands_text_list)
    if not GROUP_TRIGGER_COMMANDS:
        trigger_commands_text = "(tidak ada yang diatur di config.py)"
        example_command = "/ai"
    else:
        example_command = GROUP_TRIGGER_COMMANDS[0]

    help_text = (
        "Butuh bantuan? Berikut beberapa perintah yang bisa Anda gunakan:\n\n"
        "`/start` - Memulai atau memulai ulang bot dan mereset percakapan.\n"
        "`/reset` - Melupakan seluruh percakapan kita di chat ini.\n"
        "`/help` - Menampilkan pesan bantuan ini.\n\n"
        f"**Cara Berinteraksi dengan AI:**\n"
        f"- Di chat pribadi dengan saya, Anda bisa langsung mengirimkan pesan atau pertanyaan.\n"
        f"- Anda juga bisa mengirim gambar (dengan atau tanpa caption) untuk dijelaskan oleh AI (maks {MAX_IMAGE_INPUT} gambar per album).\n"
        f"- Di grup, Anda bisa:\n"
        f"  1. Membalas (reply) salah satu pesan saya.\n"
        f"  2. Menggunakan perintah pemicu seperti `{example_command} pertanyaan Anda`.\n\n"
        f"Perintah pemicu teks yang aktif di grup saat ini: {trigger_commands_text}"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani pesan yang berisi foto untuk fitur pemahaman gambar."""
    if not IMAGE_UNDERSTANDING_ENABLED:
        return

    message = update.message
    chat_id = message.chat_id
    user = update.effective_user
    photo_file_id = message.photo[-1].file_id
    caption = message.caption

    logger.info(f"Menerima foto dari user {user.id} ({user.first_name}) di chat {chat_id}. File ID: {photo_file_id}, Caption: '{caption}'")

    if message.media_group_id:
        media_group_id_str = str(message.media_group_id)
        logger.debug(f"Foto adalah bagian dari media group: {media_group_id_str}")

        if 'media_groups' not in context.bot_data:
            context.bot_data['media_groups'] = {}
        if chat_id not in context.bot_data['media_groups']:
            context.bot_data['media_groups'][chat_id] = {}
        if media_group_id_str not in context.bot_data['media_groups'][chat_id]:
            context.bot_data['media_groups'][chat_id][media_group_id_str] = []

        current_images_in_group = context.bot_data['media_groups'][chat_id][media_group_id_str]

        is_duplicate = any(img['message_id'] == message.message_id for img in current_images_in_group)

        if not is_duplicate and len(current_images_in_group) < MAX_IMAGE_INPUT:
            current_images_in_group.append({
                'file_id': photo_file_id,
                'caption': caption,
                'message_id': message.message_id
            })
            logger.debug(f"Foto {photo_file_id} (msg_id: {message.message_id}) ditambahkan ke media group {media_group_id_str} (via bot_data). Total: {len(current_images_in_group)}")

        elif not is_duplicate and len(current_images_in_group) >= MAX_IMAGE_INPUT:
            logger.warning(f"Media group {media_group_id_str} sudah mencapai batas {MAX_IMAGE_INPUT} gambar (via bot_data). Foto {photo_file_id} (msg_id: {message.message_id}) tidak ditambahkan.")
            notified_key = f"notified_overflow_{chat_id}_{media_group_id_str}"
            if not context.bot_data.get(notified_key):
                await message.reply_text(
                    f"Anda mengirim terlalu banyak gambar dalam satu album. Hanya {MAX_IMAGE_INPUT} gambar pertama yang akan diproses.",
                    quote=True
                )
                context.bot_data[notified_key] = True
        elif is_duplicate:
             logger.debug(f"Foto {photo_file_id} (msg_id: {message.message_id}) adalah duplikat dalam media group {media_group_id_str}, diabaikan (via bot_data).")

        job_name = f"process_media_group_{chat_id}_{media_group_id_str}"
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for old_job in current_jobs:
            old_job.schedule_removal()
            logger.debug(f"Job lama '{old_job.name}' dihapus untuk direset.")
        context.job_queue.run_once(
            process_media_group_callback,
            MEDIA_GROUP_PROCESSING_DELAY,
            data={'media_group_id': media_group_id_str, 'chat_id': chat_id, 'user_id': user.id},
            name=job_name
        )
        logger.debug(f"Job '{job_name}' dijadwalkan/direset dalam {MEDIA_GROUP_PROCESSING_DELAY} detik.")

    else:
        logger.debug(f"Foto {photo_file_id} adalah gambar tunggal.")
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            photo_tg_file = await context.bot.get_file(photo_file_id)
            image_bytes = bytes(await photo_tg_file.download_as_bytearray())

            prompt_parts = []
            text_prompt = caption if caption else DEFAULT_PROMPT_FOR_IMAGE_IF_NO_CAPTION

            if text_prompt:
                prompt_parts.append(text_prompt)

            image_part_dict = {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": image_bytes
                }
            }
            prompt_parts.append(image_part_dict)

            logger.info(f"Mengirim 1 gambar dan prompt '{text_prompt}' ke Gemini untuk chat {chat_id}.")
            gemini_reply = await gemini_client.generate_multimodal_response(
                chat_id=chat_id,
                prompt_parts=prompt_parts,
                text_prompt_for_history=text_prompt
            )

            if gemini_reply:
                await message.reply_text(gemini_reply, parse_mode=ParseMode.MARKDOWN, quote=True)
            else:
                await message.reply_text("Maaf, saya tidak bisa memproses gambar ini saat ini.", quote=True)
        except Exception as e:
            logger.error(f"Error saat memproses foto tunggal {photo_file_id} untuk chat {chat_id}: {e}", exc_info=True)
            await message.reply_text("Terjadi kesalahan saat memproses gambar Anda.", quote=True)


async def process_media_group_callback(context: CallbackContext):
    """Callback JobQueue untuk memproses media group yang sudah terkumpul."""
    job_data = context.job.data
    media_group_id_str = job_data['media_group_id']
    chat_id = job_data['chat_id']

    logger.info(f"Callback dipanggil untuk memproses media group {media_group_id_str} dari chat {chat_id}.")

    all_media_groups_for_chat = context.bot_data.get('media_groups', {}).get(chat_id, {})
    media_group_images_data = all_media_groups_for_chat.pop(media_group_id_str, None)

    context.bot_data.pop(f"notified_overflow_{chat_id}_{media_group_id_str}", None)

    if not all_media_groups_for_chat:
        context.bot_data.get('media_groups', {}).pop(chat_id, None)
    if not context.bot_data.get('media_groups'):
         context.bot_data.pop('media_groups', None)

    if not media_group_images_data:
        logger.warning(f"Tidak ada data gambar valid ditemukan (atau sudah dihapus dari bot_data) untuk media group {media_group_id_str} di chat {chat_id} pada saat callback.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    prompt_parts = []
    final_text_prompt = DEFAULT_PROMPT_FOR_IMAGE_IF_NO_CAPTION
    for img_detail in media_group_images_data:
        if img_detail.get('caption'):
            final_text_prompt = img_detail['caption']
            logger.info(f"Menggunakan caption '{final_text_prompt}' dari media group {media_group_id_str}.")
            break

    if final_text_prompt:
        prompt_parts.append(final_text_prompt)

    text_prompt_for_history = final_text_prompt

    images_processed_count = 0
    for img_detail in media_group_images_data:
        if images_processed_count >= MAX_IMAGE_INPUT:
            logger.warning(f"Mencapai batas MAX_IMAGE_INPUT ({MAX_IMAGE_INPUT}) saat memproses gambar untuk media group {media_group_id_str}")
            break
        try:
            logger.debug(f"Mengunduh file_id: {img_detail['file_id']} untuk media group {media_group_id_str}")
            photo_tg_file = await context.bot.get_file(img_detail['file_id'])
            image_bytes = bytes(await photo_tg_file.download_as_bytearray())

            image_part_dict = {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": image_bytes
                }
            }
            prompt_parts.append(image_part_dict)
            images_processed_count += 1
        except Exception as e:
            logger.error(f"Gagal mengunduh atau membuat Part untuk file_id {img_detail['file_id']} dalam media group {media_group_id_str}: {e}")

    if images_processed_count == 0:
        logger.warning(f"Tidak ada gambar yang berhasil diunduh/diproses untuk media group {media_group_id_str}.")
        first_message_id_in_group = media_group_images_data[0].get('message_id') if media_group_images_data else None
        try:
            await context.bot.send_message(chat_id, "Maaf, saya gagal memproses gambar-gambar yang Anda kirim dalam album ini.", reply_to_message_id=first_message_id_in_group)
        except Exception as send_error:
            logger.warning(f"Gagal membalas pesan pertama album, mengirim pesan biasa: {send_error}")
            await context.bot.send_message(chat_id, "Maaf, saya gagal memproses gambar-gambar yang Anda kirim dalam album ini.")
        return

    logger.info(f"Mengirim {images_processed_count} gambar dan prompt '{text_prompt_for_history}' dari media group {media_group_id_str} ke Gemini untuk chat {chat_id}.")

    try:
        gemini_reply = await gemini_client.generate_multimodal_response(
            chat_id=chat_id,
            prompt_parts=prompt_parts,
            text_prompt_for_history=text_prompt_for_history
        )

        first_message_id_in_group = media_group_images_data[0].get('message_id') if media_group_images_data else None
        reply_to_msg_id = first_message_id_in_group if first_message_id_in_group else None

        if gemini_reply:
            try:
                await context.bot.send_message(chat_id, gemini_reply, parse_mode=ParseMode.MARKDOWN, reply_to_message_id=reply_to_msg_id)
            except BadRequest as send_error:
                logger.warning(f"Gagal membalas ke pesan album ({reply_to_msg_id}), mencoba mengirim tanpa reply: {send_error}")
                await context.bot.send_message(chat_id, gemini_reply, parse_mode=ParseMode.MARKDOWN)
        else:
            err_msg = "Maaf, saya tidak bisa memproses gambar-gambar ini saat ini (tidak ada respons AI)."
            logger.warning(f"Respons Gemini kosong untuk media group {media_group_id_str}")
            try:
                await context.bot.send_message(chat_id, err_msg, reply_to_message_id=reply_to_msg_id)
            except BadRequest as send_error:
                logger.warning(f"Gagal membalas ke pesan album ({reply_to_msg_id}), mencoba mengirim tanpa reply: {send_error}")
                await context.bot.send_message(chat_id, err_msg)

    except Exception as e:
        logger.error(f"Error saat memproses media group {media_group_id_str} untuk chat {chat_id} dengan Gemini: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "Terjadi kesalahan internal saat memproses album gambar Anda.")


async def think_deeper_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Menangani perintah /td untuk meminta AI berpikir lebih mendalam."""
    message = update.message
    chat_id = message.chat_id
    user = update.effective_user

    prompt_text = ""
    target_message = message

    if context.args:
        prompt_text = " ".join(context.args)
        logger.info(f"Perintah /td dari user {user.id} di chat {chat_id} dengan argumen: {prompt_text[:50]}...")
    elif message.reply_to_message and message.reply_to_message.text:
        prompt_text = message.reply_to_message.text
        target_message = message.reply_to_message
        logger.info(f"Perintah /td dari user {user.id} di chat {chat_id} sebagai balasan ke teks: {prompt_text[:50]}...")
    else:
        await message.reply_text("Gunakan `/td <pertanyaan Anda>` atau balas pesan teks yang ingin dipikirkan lebih dalam dengan `/td`.")
        return

    if not prompt_text:
         await message.reply_text("Mohon berikan pertanyaan atau balas pesan teks yang valid.")
         return

    thinking_indicator_msg: Message | None = None
    try:
        thinking_indicator_msg = await target_message.reply_text(
            config.THINKING_INDICATOR_MESSAGE
        )
        logger.info(f"Hasil dari target_message.reply_text: Tipe={type(thinking_indicator_msg)}, Nilai={thinking_indicator_msg}")
        if thinking_indicator_msg:
             logger.info(f"Pesan indikator BERHASIL dikirim (msg_id: {thinking_indicator_msg.message_id}).")
        else:
             logger.warning("target_message.reply_text tampaknya mengembalikan nilai 'None' atau 'Falsy' tanpa error.")
    except Exception as e:
        logger.error(f"Gagal mengirim pesan indikator thinking ke chat {chat_id}: {e}", exc_info=True)

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    prompt_parts = [prompt_text]
    text_prompt_for_history = prompt_text

    gemini_reply = await gemini_client.generate_thinking_response(
        chat_id=chat_id,
        prompt_parts=prompt_parts,
        text_prompt_for_history=text_prompt_for_history
    )

    final_text = ""
    if gemini_reply:
        final_text = gemini_reply
    else:
        final_text = "Maaf, saya tidak dapat memberikan respons setelah berpikir mendalam saat ini."

    message_too_long = len(final_text) > TELEGRAM_MAX_MESSAGE_LENGTH - 10 # Cek batas panjang

    if message_too_long:
        logger.warning(f"Respons /td terlalu panjang ({len(final_text)} chars). Akan dipecah.")

        if thinking_indicator_msg:
            try:
                await context.bot.delete_message(chat_id=thinking_indicator_msg.chat_id, message_id=thinking_indicator_msg.message_id)
                logger.info(f"Pesan indikator thinking (msg_id: {thinking_indicator_msg.message_id}) dihapus karena respons panjang.")
            except Exception as del_err:
                logger.warning(f"Gagal menghapus pesan indikator thinking (msg_id: {thinking_indicator_msg.message_id}): {del_err}")


        await send_long_message(context, chat_id, final_text, reply_to_message_id=target_message.message_id, parse_mode=ParseMode.MARKDOWN)

    elif thinking_indicator_msg:
        try:
            await context.bot.edit_message_text(
                text=final_text,
                chat_id=thinking_indicator_msg.chat_id,
                message_id=thinking_indicator_msg.message_id,
                parse_mode=ParseMode.MARKDOWN
            )
            logger.info(f"Pesan indikator thinking (msg_id: {thinking_indicator_msg.message_id}) diedit dengan respons /td.")
        except Exception as edit_err:
            logger.warning(f"Gagal mengedit pesan indikator thinking (msg_id: {thinking_indicator_msg.message_id}): {edit_err}. Mengirim pesan baru.")

            await send_long_message(context, chat_id, final_text, reply_to_message_id=target_message.message_id, parse_mode=ParseMode.MARKDOWN)

    else:
         logger.warning("Indikator thinking gagal dikirim, mengirim respons /td sebagai pesan baru.")
         await send_long_message(context, chat_id, final_text, reply_to_message_id=target_message.message_id, parse_mode=ParseMode.MARKDOWN)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096

async def send_long_message(
    context: CallbackContext,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = ParseMode.MARKDOWN
):
    """Mengirim pesan teks, memecahnya jika terlalu panjang, dengan fallback Markdown."""
    if not text:
        logger.warning(f"send_long_message dipanggil dengan teks kosong untuk chat_id {chat_id}.")
        return

    chunks = []
    current_chunk = ""
    limit = TELEGRAM_MAX_MESSAGE_LENGTH - 10

    lines = text.split('\n')
    for i, line in enumerate(lines):
        if len(line) > limit:
            logger.warning(f"Satu baris terlalu panjang ({len(line)} chars) untuk dipecah dengan rapi. Akan dipecah paksa di chat {chat_id}.")
            if current_chunk:
                 chunks.append(current_chunk.strip())
                 current_chunk = ""
            for k in range(0, len(line), limit):
                chunks.append(line[k:k+limit])

        elif len(current_chunk) + len(line) + 1 <= limit:
            current_chunk += line + ('\n' if i < len(lines) - 1 else '')
        else:
            chunks.append(current_chunk.strip())
            current_chunk = line + ('\n' if i < len(lines) - 1 else '')

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    if not chunks:
        logger.error(f"Pemecahan pesan menghasilkan chunk kosong untuk chat_id {chat_id}!")
        return

    if len(chunks) > 1:
        logger.info(f"Memecah pesan menjadi {len(chunks)} bagian untuk chat_id {chat_id}.")

    first_message_sent = False
    for i, chunk in enumerate(chunks):
        if not chunk: continue

        current_reply_id = reply_to_message_id if i == 0 else None

        try:

            await context.bot.send_message(
                chat_id=chat_id,
                text=chunk,
                reply_to_message_id=current_reply_id,
                parse_mode=parse_mode
            )
            first_message_sent = True

        except RetryAfter as e:
            logger.warning(f"Terkena Rate Limit saat mengirim chunk {i+1}/{len(chunks)} ke chat {chat_id}. Menunggu {e.retry_after} detik...")
            await asyncio.sleep(e.retry_after)
            try:
                 await context.bot.send_message(chat_id=chat_id, text=chunk, reply_to_message_id=current_reply_id, parse_mode=parse_mode)
                 first_message_sent = True
            except Exception as e_retry:
                 logger.error(f"Gagal mengirim chunk {i+1}/{len(chunks)} ke chat {chat_id} setelah retry: {e_retry}")
                 break

        except BadRequest as e:

            if "Can't parse entities" in str(e):
                logger.warning(f"Gagal mengirim chunk {i+1} dengan parse_mode={parse_mode} ke chat {chat_id}: {e}. Mencoba lagi tanpa parse_mode.")
                try:

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        reply_to_message_id=current_reply_id,
                        parse_mode=None # Kirim sebagai teks biasa
                    )
                    first_message_sent = True
                    logger.info(f"Berhasil mengirim chunk {i+1} sebagai plain text setelah error parse.")
                except Exception as e_plain:
                    logger.error(f"Gagal mengirim chunk {i+1} sebagai plain text ke chat {chat_id} setelah fallback: {e_plain}")
                    break # Hentikan jika fallback juga gagal

            else:

                logger.error(f"Error BadRequest lain saat mengirim chunk {i+1}/{len(chunks)} ke chat {chat_id}: {e}")
                if i == 0:
                     try: await context.bot.send_message(chat_id=chat_id, text=f"Maaf, terjadi kesalahan saat mengirim balasan: {e}")
                     except: pass
                break

        except TelegramError as e:
            logger.error(f"Error Telegram lain saat mengirim chunk {i+1}/{len(chunks)} ke chat {chat_id}: {e}")
            if i == 0:
                 try: await context.bot.send_message(chat_id=chat_id, text=f"Maaf, terjadi kesalahan saat mengirim balasan: {e}")
                 except: pass
            break
        except Exception as e:
             logger.error(f"Error tak terduga saat mengirim chunk {i+1}/{len(chunks)} ke chat {chat_id}: {e}", exc_info=True)
             break


        if len(chunks) > 1 and i < len(chunks) - 1:
            await asyncio.sleep(0.6)
