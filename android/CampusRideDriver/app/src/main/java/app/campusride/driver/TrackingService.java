package app.campusride.driver;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.location.*;
import android.os.*;
import android.content.pm.ServiceInfo;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import org.json.JSONObject;

public final class TrackingService extends Service implements LocationListener {
  private LocationManager lm;
  private final ExecutorService io=Executors.newSingleThreadExecutor();
  private String base,cookie,csrf;private int trip;
  private volatile boolean active=false;
  @Override public void onCreate(){super.onCreate();NotificationManager nm=getSystemService(NotificationManager.class);
    nm.createNotificationChannel(new NotificationChannel("campusride_gps","Bus GPS tracking",NotificationManager.IMPORTANCE_LOW));}
  @Override public int onStartCommand(Intent it,int flags,int startId){
    if(it==null)return START_NOT_STICKY;
    base=it.getStringExtra("url");cookie=it.getStringExtra("cookie");csrf=it.getStringExtra("csrf");trip=it.getIntExtra("trip",-1);
    if(base==null||!base.startsWith("https://")||trip<1||cookie==null||csrf==null){stopSelf();return START_NOT_STICKY;}
    Notification n=new Notification.Builder(this,"campusride_gps").setSmallIcon(android.R.drawable.ic_menu_mylocation)
      .setContentTitle("CampusRide · real GPS active").setContentText("Tracking assigned bus trip #"+trip)
      .setOngoing(true).build();
    if(Build.VERSION.SDK_INT>=29)startForeground(82,n,ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);else startForeground(82,n);
    lm=(LocationManager)getSystemService(LOCATION_SERVICE);active=true;
    if(checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)!=PackageManager.PERMISSION_GRANTED&&checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION)!=PackageManager.PERMISSION_GRANTED){stopSelf();return START_NOT_STICKY;}
    try{lm.requestLocationUpdates(LocationManager.GPS_PROVIDER,5000,4,this,Looper.getMainLooper());}catch(Exception ignored){}
    try{lm.requestLocationUpdates(LocationManager.NETWORK_PROVIDER,6500,8,this,Looper.getMainLooper());}catch(Exception ignored){}
    return START_NOT_STICKY;
  }
  @Override public void onLocationChanged(Location l){if(!active||l==null)return;
    final String target=base+"/api/schedules/"+trip+"/location";
    final String userCookie=cookie,userCsrf=csrf;
    io.execute(()->{
      HttpURLConnection c=null;
      try{
        JSONObject j=new JSONObject();j.put("lat",l.getLatitude());j.put("lon",l.getLongitude());j.put("accuracy",l.getAccuracy());
        j.put("timestamp",l.getTime());j.put("speed",l.hasSpeed()?l.getSpeed():JSONObject.NULL);j.put("heading",l.hasBearing()?l.getBearing():JSONObject.NULL);
        c=(HttpURLConnection)new URL(target).openConnection();c.setRequestMethod("POST");c.setConnectTimeout(12000);c.setReadTimeout(12000);
        c.setRequestProperty("Content-Type","application/json");c.setRequestProperty("Cookie",userCookie);c.setRequestProperty("X-CSRF-Token",userCsrf);
        c.setDoOutput(true);try(OutputStream o=c.getOutputStream()){o.write(j.toString().getBytes(StandardCharsets.UTF_8));}
        int status=c.getResponseCode();if(status==200){try(InputStream in=c.getInputStream()){ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] tmp=new byte[1024];int size;while((size=in.read(tmp))!=-1)b.write(tmp,0,size);String body=b.toString("UTF-8");if(new JSONObject(body).optBoolean("auto_completed",false))stopSelf();}}
        if(status==401||status==403){stopSelf();}
        if(status==409){try(InputStream in=c.getErrorStream()){
          if(in!=null){ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] tmp=new byte[1024];int k;while((k=in.read(tmp))!=-1)b.write(tmp,0,k);
            if(b.toString("UTF-8").contains("Only an active trip"))stopSelf();}
        }}
      }catch(Exception ignored){/* network failures leave the notification active and retry on next device fix */}
      finally{if(c!=null)c.disconnect();}
    });
  }
  @Override public void onDestroy(){active=false;if(lm!=null)lm.removeUpdates(this);io.shutdownNow();super.onDestroy();}
  @Override public IBinder onBind(Intent i){return null;}
}
