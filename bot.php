<?php

$BOT_TOKEN = getenv("BOT_TOKEN");
$SUPABASE_URL = getenv("SUPABASE_URL");
$SUPABASE_KEY = getenv("SUPABASE_KEY");
$ADMIN_IDS = explode(",", getenv("ADMIN_IDS"));
$BOT_USERNAME = getenv("BOT_USERNAME");
$SITE_URL = getenv("SITE_URL");

$update = json_decode(file_get_contents("php://input"), true);

function apiRequest($method, $data){
    global $BOT_TOKEN;
    $url = "https://api.telegram.org/bot$BOT_TOKEN/$method";
    file_get_contents($url, false, stream_context_create([
        "http"=>[
            "method"=>"POST",
            "header"=>"Content-Type: application/json",
            "content"=>json_encode($data)
        ]
    ]));
}

function dbRequest($endpoint, $method="GET", $data=null){
    global $SUPABASE_URL,$SUPABASE_KEY;
    $headers = "apikey: $SUPABASE_KEY\r\nAuthorization: Bearer $SUPABASE_KEY\r\nContent-Type: application/json";
    return json_decode(file_get_contents("$SUPABASE_URL/rest/v1/$endpoint", false, stream_context_create([
        "http"=>[
            "method"=>$method,
            "header"=>$headers,
            "content"=>$data?json_encode($data):null
        ]
    ])), true);
}

$message = $update["message"] ?? null;
$callback = $update["callback_query"] ?? null;

/* ================= WEB VERIFY UI ================== */
if(isset($_GET["verify"])){
$id=$_GET["verify"];
?>
<!DOCTYPE html>
<html>
<head>
<title>Verification</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gradient-to-r from-indigo-600 to-purple-600 min-h-screen flex items-center justify-center">
<div class="bg-white p-8 rounded-xl shadow-xl text-center max-w-sm">
<h1 class="text-2xl font-bold mb-4">Verify Your Account</h1>
<p class="mb-6">Click the button to verify</p>
<button onclick="verify()" class="bg-green-500 hover:bg-green-600 text-white px-6 py-3 rounded-lg text-lg">Verify Now</button>
<p id="msg" class="mt-4 text-green-600 font-bold"></p>
</div>
<script>
function verify(){
fetch("?done=<?php echo $id ?>").then(()=>{
document.getElementById("msg").innerHTML="✅ Verified! Redirecting...";
setTimeout(()=>{ window.location.href="https://t.me/<?php echo getenv('BOT_USERNAME'); ?>"; },2000);
});
}
</script>
</body>
</html>
<?php exit; }

if(isset($_GET["done"])){
$id=$_GET["done"];
dbRequest("users?id=eq.$id","PATCH",["verified"=>true]);
echo "verified";
exit;
}

/* ================= BOT LOGIC ================== */

if($message){
$chat_id = $message["chat"]["id"];
$text = $message["text"] ?? "";

$isAdmin = in_array($chat_id,$ADMIN_IDS);

/* START */
if(strpos($text,"/start")===0){
$ref = explode(" ",$text)[1] ?? null;

$user = dbRequest("users?id=eq.$chat_id");
if(!$user){
dbRequest("users","POST",[
"id"=>$chat_id,
"referred_by"=>$ref,
"points"=>0,
"verified"=>false
]);
}

$channels = dbRequest("channels");
$buttons=[];
foreach($channels as $ch){
$buttons[]=[["text"=>"Join Channel","url"=>$ch["invite_link"]]];
}
$buttons[]=[["text"=>"✅ Joined All Channels","callback_data"=>"checkjoin"]];

apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"📢 Join all channels then click the button below:",
"reply_markup"=>["inline_keyboard"=>$buttons]
]);
}

/* STATS */
if($text=="📊 Stats"){
$user=dbRequest("users?id=eq.$chat_id")[0];
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"👤 Your Stats\n\nPoints: ".$user["points"]]);
}

/* REFERRAL LINK */
if($text=="🔗 Referral Link"){
$link="https://t.me/$BOT_USERNAME?start=$chat_id";
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Your referral link:\n$link"]);
}

/* WITHDRAW */
if($text=="💸 Withdraw"){
$user=dbRequest("users?id=eq.$chat_id")[0];
$settings=dbRequest("settings?id=eq.1")[0];

if($user["points"] < $settings["withdraw_points"]){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ Not enough points. Need ".$settings["withdraw_points"]]);
exit;
}

$coupon=dbRequest("coupons?limit=1");
if(!$coupon){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ No coupons in stock"]);
exit;
}

$code=$coupon[0]["code"];
dbRequest("redeems","POST",["user_id"=>$chat_id,"coupon"=>$code]);
dbRequest("coupons?id=eq.".$coupon[0]["id"],"DELETE");

dbRequest("users?id=eq.$chat_id","PATCH",["points"=>$user["points"]-$settings["withdraw_points"]]);

apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"🎉 Your coupon:\n$code"]);

foreach($ADMIN_IDS as $admin){
apiRequest("sendMessage",["chat_id"=>$admin,"text"=>"User $chat_id redeemed coupon: $code"]);
}
}

/* ADMIN COMMANDS */

if($isAdmin && strpos($text,"/addchannel")===0){
$link=explode(" ",$text)[1]??null;
if(!$link){ apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Usage: /addchannel https://t.me/channel"]); exit; }
dbRequest("channels","POST",["invite_link"=>$link]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Channel added"]);
}

if($isAdmin && strpos($text,"/removechannel")===0){
$link=explode(" ",$text)[1]??null;
file_get_contents("$SUPABASE_URL/rest/v1/channels?invite_link=eq.$link", false, stream_context_create([
"http"=>["method"=>"DELETE","header"=>"apikey: $SUPABASE_KEY\r\nAuthorization: Bearer $SUPABASE_KEY"]
]));
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Channel removed"]);
}

if($isAdmin && strpos($text,"/setpoints")===0){
$points=explode(" ",$text)[1]??null;
dbRequest("settings?id=eq.1","PATCH",["withdraw_points"=>$points]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Withdraw points updated to $points"]);
}

if($isAdmin && $text=="/redeemlog"){
$logs=dbRequest("redeems?order=created_at.desc&limit=10");
$msg="📜 Last 10 Redeems:\n";
foreach($logs as $l){
$msg.="User ".$l["user_id"]." - ".$l["coupon"]."\n";
}
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>$msg]);
}

if($isAdmin && $text=="/stock"){
$count=dbRequest("coupons?select=count", "GET");
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"📦 Coupons in stock: ".count($count)]);
}

}

/* CALLBACK */
if($callback){
$data=$callback["data"];
$chat_id=$callback["message"]["chat"]["id"];

if($data=="checkjoin"){
$channels=dbRequest("channels");
foreach($channels as $ch){
$member=json_decode(file_get_contents("https://api.telegram.org/bot$BOT_TOKEN/getChatMember?chat_id=".$ch["invite_link"]."&user_id=$chat_id"),true);
if($member["result"]["status"]=="left"){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ You did not join all channels"]);
exit;
}
}

apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"✅ Channels verified. Please verify:",
"reply_markup"=>["inline_keyboard"=>[
[["text"=>"Verify Now","url"=>"$SITE_URL?verify=$chat_id"]],
[["text"=>"Complete Verification","callback_data"=>"complete"]]
]]
]);
}

if($data=="complete"){
$user=dbRequest("users?id=eq.$chat_id")[0];
if(!$user["verified"]){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ Verification not completed"]);
}else{
apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"🎉 Welcome!",
"reply_markup"=>["keyboard"=>[
[["text"=>"📊 Stats"],["text"=>"🔗 Referral Link"]],
[["text"=>"💸 Withdraw"],["text"=>"📦 Stock"]]
],"resize_keyboard"=>true]
]);
}
}
}

?>
