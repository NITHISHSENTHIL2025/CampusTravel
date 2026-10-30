package app.campusride.student;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Build;
import android.webkit.*;
import android.view.*;
import android.widget.*;
import android.graphics.Color;
import android.net.Uri;
import android.annotation.SuppressLint;

public final class MainActivity extends Activity {
  private WebView browser;
  private String server="";
  private final String section="/student";
  private static final int REQ=202;
  @Override public void onCreate(Bundle state){super.onCreate(state);
    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,Manifest.permission.ACCESS_COARSE_LOCATION},REQ);
    server=getPreferences(MODE_PRIVATE).getString("server","https://campustravel.onrender.com");
    if(server.isEmpty())configure();else openBrowser();
  }
  private void configure(){
    LinearLayout l=new LinearLayout(this);l.setOrientation(LinearLayout.VERTICAL);l.setPadding(35,80,35,35);l.setBackgroundColor(Color.WHITE);
    TextView title=new TextView(this);title.setText("CampusRide Student");title.setTextColor(Color.BLACK);title.setTextSize(28);l.addView(title);
    TextView hint=new TextView(this);hint.setText("Enter the HTTPS address of the SAME CampusRide server used by your admin laptop. Example: https://your-domain.example\n\nNever enter a random website. Driver GPS requires the authenticated CampusRide server.");hint.setPadding(0,20,0,25);hint.setTextSize(15);l.addView(hint);
    EditText edit=new EditText(this);edit.setSingleLine(true);edit.setHint("https://your-secure-campuride-url");edit.setInputType(17);edit.setText(server);l.addView(edit);
    Button save=new Button(this);save.setText("CONNECT TO MY CAMPUSRIDE");l.addView(save);
    TextView err=new TextView(this);err.setTextColor(Color.RED);l.addView(err);
    save.setOnClickListener(v->{String u=edit.getText().toString().trim();Uri p=Uri.parse(u);
      if(!"https".equalsIgnoreCase(p.getScheme()) || p.getHost()==null || p.getHost().isEmpty() || p.getUserInfo()!=null){err.setText("HTTPS server URL required.");return;}
      server=u.replaceAll("/+$","");getPreferences(MODE_PRIVATE).edit().putString("server",server).apply();openBrowser();});
    setContentView(l);
  }
  @SuppressLint("SetJavaScriptEnabled") private void openBrowser(){
    browser=new WebView(this);browser.setBackgroundColor(Color.WHITE);setContentView(browser);
    browser.getSettings().setJavaScriptEnabled(true);browser.getSettings().setDomStorageEnabled(true);browser.getSettings().setGeolocationEnabled(true);
    browser.getSettings().setAllowFileAccess(false);browser.getSettings().setAllowContentAccess(false);
    browser.getSettings().setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
    browser.setWebViewClient(new WebViewClient(){
      @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request){
        String target=request.getUrl().toString();return !target.startsWith(server+"/")&&!target.equals(server);
      }
    });
    browser.setWebChromeClient(new WebChromeClient(){
      @Override public void onGeolocationPermissionsShowPrompt(String origin,GeolocationPermissions.Callback cb){
        cb.invoke(origin,origin.equals(Uri.parse(server).getScheme()+"://"+Uri.parse(server).getAuthority()),false);
      }
      @Override public void onPermissionRequest(PermissionRequest request){
        // Camera only for authorized first-party HTTPS page.
        if(request.getOrigin().toString().startsWith(server+"/"))request.grant(request.getResources());else request.deny();
      }
    });
    browser.addJavascriptInterface(new Bridge(),"CampusNative");
    browser.loadUrl(server+section);
  }
  public final class Bridge {
    @JavascriptInterface public void changeServer(){runOnUiThread(()->configure());}
    @JavascriptInterface public void startTracking(int tripId,String csrf){}
    @JavascriptInterface public void stopTracking(){}

  }
  @Override public void onBackPressed(){if(browser!=null&&browser.canGoBack())browser.goBack();else super.onBackPressed();}
  @Override public void onDestroy(){if(browser!=null)browser.destroy();super.onDestroy();}
}
