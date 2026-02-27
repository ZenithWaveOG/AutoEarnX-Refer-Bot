<?php
// Shein Telegram Referral Bot - Single PHP File for Render + Supabase
// Replace these with your actual values
$BOT_TOKEN = '8160133246:AAFDhth4g5hsSumyXWony2j81hJVHkA3zwk';
$SUPABASE_URL = 'https://unlmerrawfgybmkytebu.supabase.co';
$SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVubG1lcnJhd2ZneWJta3l0ZWJ1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIxMzE4OTIsImV4cCI6MjA4NzcwNzg5Mn0.WQmV6qJcx2sZ3ZjjhMgUZUaFuumGo68rkNI9-1UCOMQ';
$SUPABASE_SERVICE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVubG1lcnJhd2ZneWJta3l0ZWJ1Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MjEzMTg5MiwiZXhwIjoyMDg3NzA3ODkyfQ.ezV9b3KHpFLkdNXBw_yq8tjsmntlPmEgdSEkGTh-8j8y';
$ADMIN_ID = 8537079657; // Your Telegram user ID
$WEBHOOK_SECRET = 'mysecret123';
$DOMAIN = 'https://autoearnx-refer-bot.onrender.com'; // Your Render domain

// Supabase helper functions using cURL
function supabase_select($table, $filters = []) {
    global $SUPABASE_URL, $SUPABASE_ANON_KEY;
    $url = $SUPABASE_URL . '/rest/v1/' . $table . '?select=*';
    foreach ($filters as $col => $val) {
        $url .= "&$col=eq." . urlencode($val);
    }
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'apikey: ' . $SUPABASE_ANON_KEY,
        'Authorization: Bearer ' . $SUPABASE_ANON_KEY
    ]);
    $result = curl_exec($ch);
    curl_close($ch);
    return json_decode($result, true) ?: [];
}

function supabase_upsert($table, $data) {
    global $SUPABASE_URL, $SUPABASE_SERVICE_KEY;
    $url = $SUPABASE_URL . '/rest/v1/' . $table;
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'apikey: ' . $SUPABASE_SERVICE_KEY,
        'Authorization: Bearer ' . $SUPABASE_SERVICE_KEY,
        'Content-Type: application/json',
        'Prefer: resolution=merge-duplicates'
    ]);
    $result = curl_exec($ch);
    curl_close($ch);
    return json_decode($result, true);
}

function supabase_delete($table, $filters) {
    global $SUPABASE_URL, $SUPABASE_SERVICE_KEY;
    $url = $SUPABASE_URL . '/rest/v1/' . $table . '?';
    foreach ($filters as $col => $val) {
        $url .= "$col=eq." . urlencode($val) . '&';
    }
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'DELETE');
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'apikey: ' . $SUPABASE_SERVICE_KEY,
        'Authorization: Bearer ' . $SUPABASE_SERVICE_KEY
    ]);
    curl_exec($ch);
    curl_close($ch);
}

// Telegram API helper
function apiRequest($method, $params) {
    global $BOT_TOKEN;
    $url = "https://api.telegram.org/bot$BOT_TOKEN/$method";
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $params);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    $result = curl_exec($ch);
    curl_close($ch);
    return json_decode($result, true);
}

function sendMessage($chatId, $text, $replyMarkup = null) {
    $params = [
        'chat_id' => $chatId,
        'text' => $text,
        'parse_mode' => 'HTML'
    ];
    if ($replyMarkup) $params['reply_markup'] = json_encode($replyMarkup);
    return apiRequest('sendMessage', $params);
}

function editMessageText($chatId, $messageId, $text, $replyMarkup = null) {
    $params = [
        'chat_id' => $chatId,
        'message_id' => $messageId,
        'text' => $text,
        'parse_mode' => 'HTML'
    ];
    if ($replyMarkup) $params['reply_markup'] = json_encode($replyMarkup);
    return apiRequest('editMessageText', $params);
}

function answerCallbackQuery($callbackQueryId, $text = '') {
    global $BOT_TOKEN;
    $url = "https://api.telegram.org/bot$BOT_TOKEN/answerCallbackQuery?callback_query_id=$callbackQueryId&text=" . urlencode($text);
    file_get_contents($url);
}

// Check if user joined channel
function isMember($chatId, $channel) {
    $params = ['chat_id' => $channel, 'user_id' => $chatId];
    return apiRequest('getChatMember', $params)['ok'];
}

// Get channels from DB
function getChannels() {
    return supabase_select('channels');
}

// Main bot logic
$update = json_decode(file_get_contents('php://input'), true);
if (!$update) exit;

$chatId = $update['message']['chat']['id'] ?? $update['callback_query']['message']['chat']['id'] ?? 0;
$userId = $update['message']['from']['id'] ?? $update['callback_query']['from']['id'] ?? 0;
$username = $update['message']['from']['username'] ?? $update['callback_query']['from']['username'] ?? '';
$text = $update['message']['text'] ?? '';
$callbackData = $update['callback_query']['data'] ?? '';
$messageId = $update['callback_query']['message']['message_id'] ?? 0;
$callbackQueryId = $update['callback_query']['id'] ?? '';

$user = supabase_select('users', ['user_id' => $userId])[0] ?? null;

// Admin check
$isAdmin = $userId == $ADMIN_ID;

// Handle states (using user state in DB)
if ($user && isset($user['state'])) {
    handleState($user, $text, $chatId);
    exit;
}

// Referral handling for /start ref_XXXX
if (strpos($text, '/start ref_') === 0) {
    $refCode = substr($text, 10);
    $referrer = supabase_select('users', ['ref_code' => $refCode])[0];
    if ($referrer && $referrer['user_id'] != $userId) {
        supabase_upsert('users', ['user_id' => $referrer['user_id'], 'points' => $referrer['points'] + 1, 'referrals' => $referrer['referrals'] + 1]);
        sendMessage($chatId, "🎉 Thanks for joining via referral! You get 1 point credited to referrer.");
        sendMessage($referrer['user_id'], "🎉 Congratulations! New user joined with your link. +1 point!");
    }
    $text = '/start';
}

// Main handlers
if ($text == '/start') {
    $channels = getChannels();
    if (empty($channels)) {
        sendMessage($chatId, "No channels set by admin.");
        exit;
    }
    
    $joinKeyboard = ['inline_keyboard' => []];
    foreach ($channels as $ch) {
        $joinKeyboard['inline_keyboard'][] = [['text' => '📢 Join ' . $ch['name'], 'url' => $ch['invite_link']]];
    }
    $joinKeyboard['inline_keyboard'][] = [['text' => '✅ Joined All Channels', 'callback_data' => 'check_joins']];
    
    sendMessage($chatId, "👋 Welcome to Shein Referral Bot!\n\nPlease join all channels below then click ✅", $joinKeyboard);
    exit;
}

if ($callbackData == 'check_joins') {
    answerCallbackQuery($callbackQueryId);
    $channels = getChannels();
    $allJoined = true;
    foreach ($channels as $ch) {
        if (!isMember($userId, $ch['channel_id'])) {
            $allJoined = false;
            break;
        }
    }
    
    if (!$allJoined) {
        editMessageText($chatId, $messageId, "❌ You haven\'t joined all channels! Please join and try again.", null);
    } else {
        $verifyKeyboard = [
            'inline_keyboard' => [
                [['text' => '🔐 Verify Now', 'url' => $DOMAIN . '/verify.php?user=' . $userId]],
                [['text' => '⏭ Skip (for testing)', 'callback_data' => 'complete_verif']]
            ]
        ];
        editMessageText($chatId, $messageId, "✅ All channels joined!\n\nClick <b>Verify Now</b> to complete phone verification.", $verifyKeyboard);
        
        // Upsert user with step: waiting verification
        supabase_upsert('users', [
            'user_id' => $userId,
            'username' => $username,
            'ref_code' => 'ref_' . uniqid(),
            'points' => 0,
            'referrals' => 0,
            'verified' => false,
            'step' => 'waiting_verify'
        ]);
    }
    exit;
}

if ($callbackData == 'complete_verif') {
    answerCallbackQuery($callbackQueryId);
    $user = supabase_select('users', ['user_id' => $userId])[0];
    if (!$user || !$user['verified']) {
        editMessageText($chatId, $messageId, "❌ Verification not completed!");
    } else {
        editMessageText($chatId, $messageId, "✅ Verified! Welcome to menu.", getMainKeyboard());
        supabase_upsert('users', ['user_id' => $userId, 'step' => 'menu']);
    }
    exit;
}

// Main menu reply keyboards
function getMainKeyboard() {
    return [
        'keyboard' => [
            [['text' => '📊 Stats']],
            [['text' => '🔗 Referral Link']],
            [['text' => '💰 Withdraw']],
            [['text' => '📦 Stock']]
        ],
        'resize_keyboard' => true
    ];
}

function getAdminKeyboard() {
    return [
        'keyboard' => [
            [['text' => '➕ Add Coupon']],
            [['text' => '➖ Remove Coupon']],
            [['text' => '📢 Add Channel']],
            [['text' => '📢 Remove Channel']],
            [['text' => '⚙️ Change Points']],
            [['text' => '📋 Redeems Log']],
            [['text' => '📦 Stock Admin']]
        ],
        'resize_keyboard' => true
    ];
}

// Stats
if ($text == '📊 Stats') {
    $user = supabase_select('users', ['user_id' => $userId])[0];
    sendMessage($chatId, "📊 Your Stats:\nPoints: {$user['points']}\nReferrals: {$user['referrals']}", getMainKeyboard());
    exit;
}

// Referral link
if ($text == '🔗 Referral Link') {
    $user = supabase_select('users', ['user_id' => $userId])[0];
    $link = "https://t.me/" . explode(':', $BOT_TOKEN)[0] . "?start=ref_{$user['ref_code']}";
    sendMessage($chatId, "🔗 Your unique referral link:\n<code>$link</code>\nShare and earn 1 point per join!", getMainKeyboard());
    exit;
}

// Withdraw
if ($text == '💰 Withdraw') {
    $settings = supabase_select('settings')[0];
    $pointsNeeded = $settings['withdraw_points'] ?? 5;
    $user = supabase_select('users', ['user_id' => $userId])[0];
    if ($user['points'] < $pointsNeeded) {
        sendMessage($chatId, "❌ Not enough points! Need $pointsNeeded points.", getMainKeyboard());
    } else {
        $coupons = supabase_select('coupons', ['used' => false]);
        if (empty($coupons)) {
            sendMessage($chatId, "❌ No coupons available!", getMainKeyboard());
            exit;
        }
        $coupon = array_shift($coupons);
        
        // Mark used and log
        supabase_upsert('coupons', ['id' => $coupon['id'], 'used' => true]);
        supabase_upsert('redeems', [
            'user_id' => $userId,
            'username' => $username,
            'coupon' => $coupon['code'],
            'time' => date('Y-m-d H:i:s')
        ]);
        
        // Deduct points
        supabase_upsert('users', ['user_id' => $userId, 'points' => $user['points'] - $pointsNeeded]);
        
        // Notify admin
        sendMessage($ADMIN_ID, "💰 User @$username ($userId) redeemed coupon: {$coupon['code']} at " . date('Y-m-d H:i:s'));
        
        sendMessage($chatId, "✅ Coupon redeemed!\n<code>{$coupon['code']}</code>", getMainKeyboard());
    }
    exit;
}

// Stock
if ($text == '📦 Stock') {
    $settings = supabase_select('settings')[0];
    sendMessage($chatId, "📦 Available Stock:\n500 off on 500: {$settings['stock_500'] ?? 0}", getMainKeyboard());
    exit;
}

// Admin section
if ($isAdmin && $text == 'Admin Menu') {
    sendMessage($chatId, "⚙️ Admin Panel", getAdminKeyboard());
    exit;
}

if ($isAdmin) {
    if ($text == '➕ Add Coupon') {
        supabase_upsert('users', ['user_id' => $userId, 'state' => 'add_coupon']);
        sendMessage($chatId, "Send coupons (one per line):", getAdminKeyboard());
        exit;
    }
    
    if ($text == '➖ Remove Coupon') {
        supabase_upsert('users', ['user_id' => $userId, 'state' => 'remove_coupon']);
        sendMessage($chatId, "How many coupons to remove?", getAdminKeyboard());
        exit;
    }
    
    if ($text == '📢 Add Channel') {
        sendMessage($chatId, "Send /addchannel <invite_link> <channel_username>", getAdminKeyboard());
        exit;
    }
    
    if ($text == '📢 Remove Channel') {
        sendMessage($chatId, "Send /removechannel <invite_link>", getAdminKeyboard());
        exit;
    }
    
    if ($text == '⚙️ Change Points') {
        supabase_upsert('users', ['user_id' => $userId, 'state' => 'change_points']);
        sendMessage($chatId, "Enter new withdraw points:", getAdminKeyboard());
        exit;
    }
    
    if ($text == '📋 Redeems Log') {
        $logs = supabase_select('redeems', [], 10); // Assume limit via query param if supported
        $logText = "Last 10 redeems:\n";
        foreach (array_reverse($logs) as $log) {
            $logText .= "- {$log['username']}: {$log['coupon']} (" . $log['time'] . ")\n";
        }
        sendMessage($chatId, $logText, getAdminKeyboard());
        exit;
    }
    
    if ($text == '📦 Stock Admin') {
        $settings = supabase_select('settings')[0];
        sendMessage($chatId, "Current stock:\n500 off: {$settings['stock_500']}\n\nSet new? Reply with stock number.", getAdminKeyboard());
        exit;
    }
}

// State handler
function handleState($user, $text, $chatId) {
    global $isAdmin;
    $state = $user['state'];
    
    if ($state == 'add_coupon') {
        $coupons = explode("\n", $text);
        foreach ($coupons as $code) {
            $code = trim($code);
            if ($code) supabase_upsert('coupons', ['code' => $code, 'used' => false]);
        }
        sendMessage($chatId, "✅ Coupons added!");
        supabase_upsert('users', ['user_id' => $user['user_id'], 'state' => null]);
    }
    
    if ($state == 'remove_coupon') {
        $num = (int)$text;
        $coupons = supabase_select('coupons', ['used' => false]);
        for ($i = 0; $i < min($num, count($coupons)); $i++) {
            supabase_delete('coupons', ['id' => $coupons[$i]['id']]);
        }
        sendMessage($chatId, "$num coupons removed!");
        supabase_upsert('users', ['user_id' => $user['user_id'], 'state' => null]);
    }
    
    if ($state == 'change_points') {
        supabase_upsert('settings', ['withdraw_points' => (int)$text]);
        sendMessage($chatId, "Withdraw points set to $text");
        supabase_upsert('users', ['user_id' => $user['user_id'], 'state' => null]);
    }
    
    // Add more states...
    
    supabase_upsert('users', ['user_id' => $user['user_id'], 'step' => 'menu']);
}

// Admin commands
if (strpos($text, '/addchannel ') === 0 && $isAdmin) {
    $parts = explode(' ', $text, 3);
    $link = $parts[1];
    $channelId = str_replace('https://t.me/+', '', $link); // Parse channel ID
    $name = $parts[2] ?? basename($link);
    supabase_upsert('channels', ['channel_id' => $channelId, 'invite_link' => $link, 'name' => $name]);
    sendMessage($chatId, "✅ Channel added!");
    exit;
}

if (strpos($text, '/removechannel ') === 0 && $isAdmin) {
    $link = substr($text, 15);
    $channelId = str_replace('https://t.me/+', '', $link);
    supabase_delete('channels', ['invite_link' => $link]);
    sendMessage($chatId, "✅ Channel removed!");
    exit;
}

// Default menu
if ($user && $user['verified']) {
    sendMessage($chatId, "📋 Select option:", getMainKeyboard());
}

// Database tables to create in Supabase (SQL):
// users: user_id(int8), username(text), ref_code(text), points(int4), referrals(int4), verified(bool), step(text), state(text)
// channels: channel_id(text), invite_link(text), name(text)
// coupons: id(uuid), code(text), used(bool)
// settings: withdraw_points(int4), stock_500(int4)
// redeems: id(uuid), user_id(int8), username(text), coupon(text), time(timestamptz)

// For verify.php (separate file needed for web verification):
// Use Supabase auth or simple phone check, update verified=true
// Example: POST phone, check uniqueness, set verified

// Deploy: Set webhook https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=$DOMAIN/$WEBHOOK_SECRET
?>
