<?php

$BOT_TOKEN = getenv("BOT_TOKEN");
$SUPABASE_URL = getenv("SUPABASE_URL");
$SUPABASE_KEY = getenv("SUPABASE_KEY");
$ADMIN_IDS = explode(",", getenv("ADMIN_IDS"));
$BOT_USERNAME = getenv("BOT_USERNAME");
$SITE_URL = getenv("SITE_URL");

$update = json_decode(file_get_contents("php://input"), true);

/* ================= FUNCTIONS ================= */

function apiRequest($method,$data){
    global $BOT_TOKEN;
    file_get_contents("https://api.telegram.org/bot$BOT_TOKEN/$method", false, stream_context_create([
        "http"=>[
            "method"=>"POST",
            "header"=>"Content-Type: application/json",
            "content"=>json_encode($data)
        ]
    ]));
}

function db($endpoint,$method="GET",$data=null){
    global $SUPABASE_URL,$SUPABASE_KEY;
    return json_decode(file_get_contents("$SUPABASE_URL/rest/v1/$endpoint", false, stream_context_create([
        "http"=>[
            "method"=>$method,
            "header"=>"apikey: $SUPABASE_KEY\r\nAuthorization: Bearer $SUPABASE_KEY\r\nContent-Type: application/json",
            "content"=>$data?json_encode($data):null
        ]
    ])),true);
}

/* ================= WEB VERIFY ================= */

if(isset($_GET["verify"])){
$id=$_GET["verify"];
?>
<!DOCTYPE html>
<html>
<head>
<title>Verification</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gradient-to-r from-indigo-600 to-purple-600 min-h-screen flex justify-center items-center">
<div class="bg-white p-8 rounded-xl shadow-xl text-center">
<h1 class="text-2xl font-bold mb-4">Account Verification</h1>
<button onclick="verify()" class="bg-green-500 hover:bg-green-600 text-white px-6 py-3 rounded-lg">Verify Now</button>
<p id="msg" class="mt-4 font-bold"></p>
</div>

<script>
async function verify(){
let fingerprint = btoa(navigator.userAgent + screen.width + screen.height);
let res = await fetch("?done=<?php echo $id ?>&device="+fingerprint);
let txt = await res.text();
document.getElementById("msg").innerHTML = txt;

if(txt.includes("Verified")){
 setTimeout(()=>{ window.location.href="https://t.me/<?php echo getenv('BOT_USERNAME');?>"; },2000);
}
}
</script>
</body>
</html>
<?php exit; }


if(isset($_GET["done"])){
$id=$_GET["done"];
$device=$_GET["device"];

// block same device
$exist=db("users?device_hash=eq.$device");
if($exist){
echo "❌ This device already used.";
exit;
}

// verify user
db("users?id=eq.$id","PATCH",[
"verified"=>true,
"device_hash"=>$device
]);

// referral reward
$user=db("users?id=eq.$id")[0];

if($user["referred_by"]){
$ref=$user["referred_by"];
$refUser=db("users?id=eq.$ref")[0];

db("users?id=eq.$ref","PATCH",[
"points"=>$refUser["points"]+1
]);

apiRequest("sendMessage",[
"chat_id"=>$ref,
"text"=>"🎉 New referral joined! +1 point"
]);
}

echo "✅ Verified successfully!";
exit;
}

/* ================= BOT ================= */

$message=$update["message"]??null;
$callback=$update["callback_query"]??null;

function userMenu($chat_id,$isAdmin){
$menu=[
[["text"=>"📊 My Stats"],["text"=>"🔗 Referral Link"]],
[["text"=>"💸 Withdraw"],["text"=>"📦 Stock"]]
];
if($isAdmin){ $menu[]=[["text"=>"⚙️ Admin Panel"]]; }

apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"🏠 Main Menu",
"reply_markup"=>["keyboard"=>$menu,"resize_keyboard"=>true]
]);
}

function adminMenu($chat_id){
apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"⚙️ Admin Panel",
"reply_markup"=>["keyboard"=>[
[["text"=>"➕ Add Coupon"],["text"=>"➖ Remove Coupon"]],
[["text"=>"➕ Add Channel"],["text"=>"➖ Remove Channel"]],
[["text"=>"✏ Set Withdraw Points"]],
[["text"=>"📜 Redeem Logs"],["text"=>"📦 Stock"]],
[["text"=>"⬅ Back to User Menu"]]
],"resize_keyboard"=>true]
]);
}

/* ================= CALLBACK ================= */

if($callback){
$chat_id=$callback["message"]["chat"]["id"];

if($callback["data"]=="checkjoin"){
$channels=db("channels");
foreach($channels as $ch){
$member=json_decode(file_get_contents("https://api.telegram.org/bot".$GLOBALS["BOT_TOKEN"]."/getChatMember?chat_id=".$ch["invite_link"]."&user_id=$chat_id"),true);
if($member["result"]["status"]=="left"){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ Join all channels first"]);
exit;
}
}

apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"✅ Channels verified",
"reply_markup"=>["inline_keyboard"=>[
[["text"=>"🚀 Verify Now","url"=>$GLOBALS["SITE_URL"]."?verify=$chat_id"]],
[["text"=>"✅ Complete Verification","callback_data"=>"complete"]]
]]
]);
}

if($callback["data"]=="complete"){
$user=db("users?id=eq.$chat_id")[0];
$isAdmin=in_array($chat_id,$GLOBALS["ADMIN_IDS"]);

if(!$user["verified"]){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ Verification not completed"]);
}else{
userMenu($chat_id,$isAdmin);
}
}
}

/* ================= MESSAGE ================= */

if($message){
$chat_id=$message["chat"]["id"];
$text=trim($message["text"]??"");
$isAdmin=in_array($chat_id,$ADMIN_IDS);

/* STATE HANDLER */
$state=db("state?id=eq.$chat_id");
if($state){
$action=$state[0]["action"];

if($action=="addcoupon"){
foreach(explode("\n",$text) as $c){
$c=trim($c);
if($c!=""){ db("coupons","POST",["code"=>$c]); }
}
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Coupons added"]);
}

if($action=="removecoupon"){
$num=intval($text);
$coupons=db("coupons?limit=$num");
foreach($coupons as $c){
db("coupons?id=eq.".$c["id"],"DELETE");
}
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Removed $num coupons"]);
}

if($action=="addchannel"){
db("channels","POST",["invite_link"=>$text]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Channel added"]);
}

if($action=="removechannel"){
file_get_contents($GLOBALS["SUPABASE_URL"]."/rest/v1/channels?invite_link=eq.$text", false, stream_context_create([
"http"=>["method"=>"DELETE","header"=>"apikey: ".$GLOBALS["SUPABASE_KEY"]."\r\nAuthorization: Bearer ".$GLOBALS["SUPABASE_KEY"]]
]));
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Channel removed"]);
}

if($action=="setpoints"){
db("settings?id=eq.1","PATCH",["withdraw_points"=>intval($text)]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"✅ Withdraw points updated"]);
}

file_get_contents($GLOBALS["SUPABASE_URL"]."/rest/v1/state?id=eq.$chat_id", false, stream_context_create([
"http"=>["method"=>"DELETE","header"=>"apikey: ".$GLOBALS["SUPABASE_KEY"]."\r\nAuthorization: Bearer ".$GLOBALS["SUPABASE_KEY"]]
]));
exit;
}

/* START */

if(strpos($text,"/start")===0){
$ref=explode(" ",$text)[1]??null;

$user=db("users?id=eq.$chat_id");
if(!$user){
db("users","POST",[
"id"=>$chat_id,
"referred_by"=>$ref,
"points"=>0,
"verified"=>false
]);
}

$channels=db("channels");
$buttons=[];
foreach($channels as $ch){
$buttons[]=[["text"=>"📢 Join Channel","url"=>$ch["invite_link"]]];
}
$buttons[]=[["text"=>"✅ Joined All Channels","callback_data"=>"checkjoin"]];

apiRequest("sendMessage",[
"chat_id"=>$chat_id,
"text"=>"Join all channels then verify:",
"reply_markup"=>["inline_keyboard"=>$buttons]
]);
}

/* USER BUTTONS */

if($text=="📊 My Stats"){
$user=db("users?id=eq.$chat_id")[0];
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"📊 Points: ".$user["points"]]);
}

if($text=="🔗 Referral Link"){
$link="https://t.me/$BOT_USERNAME?start=$chat_id";
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Your link:\n$link"]);
}

if($text=="📦 Stock"){
$coupons=db("coupons");
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"📦 Stock: ".count($coupons)]);
}

if($text=="💸 Withdraw"){
$user=db("users?id=eq.$chat_id")[0];
$settings=db("settings?id=eq.1")[0];

if($user["points"]<$settings["withdraw_points"]){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ Need ".$settings["withdraw_points"]." points"]);
exit;
}

$coupon=db("coupons?limit=1");
if(!$coupon){
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"❌ No stock"]);
exit;
}

$code=$coupon[0]["code"];
db("redeems","POST",["user_id"=>$chat_id,"coupon"=>$code]);
db("coupons?id=eq.".$coupon[0]["id"],"DELETE");

db("users?id=eq.$chat_id","PATCH",["points"=>$user["points"]-$settings["withdraw_points"]]);

apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"🎉 Coupon: $code"]);

foreach($ADMIN_IDS as $a){
apiRequest("sendMessage",["chat_id"=>$a,"text"=>"User $chat_id redeemed $code"]);
}
}

/* ADMIN MENU */

if($isAdmin && $text=="⚙️ Admin Panel"){ adminMenu($chat_id); }
if($isAdmin && $text=="⬅ Back to User Menu"){ userMenu($chat_id,true); }

if($isAdmin && $text=="➕ Add Coupon"){
db("state","POST",["id"=>$chat_id,"action"=>"addcoupon"]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Send coupons (one per line)"]);
}

if($isAdmin && $text=="➖ Remove Coupon"){
db("state","POST",["id"=>$chat_id,"action"=>"removecoupon"]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Send number to remove"]);
}

if($isAdmin && $text=="➕ Add Channel"){
db("state","POST",["id"=>$chat_id,"action"=>"addchannel"]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Send channel link"]);
}

if($isAdmin && $text=="➖ Remove Channel"){
db("state","POST",["id"=>$chat_id,"action"=>"removechannel"]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Send channel link"]);
}

if($isAdmin && $text=="✏ Set Withdraw Points"){
db("state","POST",["id"=>$chat_id,"action"=>"setpoints"]);
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>"Send new withdraw points"]);
}

if($isAdmin && $text=="📜 Redeem Logs"){
$logs=db("redeems?order=created_at.desc&limit=10");
$msg="📜 Last 10 redeems:\n";
foreach($logs as $l){
$msg.="User ".$l["user_id"]." - ".$l["coupon"]."\n";
}
apiRequest("sendMessage",["chat_id"=>$chat_id,"text"=>$msg]);
}

}

?>
