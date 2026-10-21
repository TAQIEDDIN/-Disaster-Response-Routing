/**
 * Firebase Konfigurasyonu
 * =======================
 * ADIMLAR:
 * 1. https://console.firebase.google.com adresine git
 * 2. Projenize tiklayip Project Settings'e gidin
 * 3. "Your apps" bolumunden Web uygulamanizi secin (yoksa "Add app" > Web ile olusturun)
 * 4. Add your own Firebase web app values locally if you use the optional legacy
 *    Firestore integration. Never commit real project values or credentials.
 * 5. Configure Firestore security rules for your app; do not use public
 *    allow-read/allow-write rules. The main incident API works without Firebase.
 */

const firebaseConfig = {
  apiKey: "BURAYA_API_KEY",
  authDomain: "YOUR_FIREBASE_PROJECT.firebaseapp.com",
  projectId: "YOUR_FIREBASE_PROJECT_ID",
  storageBucket: "YOUR_FIREBASE_PROJECT.appspot.com",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_FIREBASE_APP_ID"
};

// Firebase baslatma — SDK HTML'de yuklendikten sonra calisir
let db = null;

function initFirebase() {
    try {
        if (typeof firebase !== 'undefined' && firebaseConfig.apiKey !== "BURAYA_API_KEY") {
            firebase.initializeApp(firebaseConfig);
            db = firebase.firestore();
            console.log("Firebase Firestore baglantisi basarili.");
            return true;
        } else if (firebaseConfig.apiKey === "BURAYA_API_KEY") {
            console.warn("Firebase yapilandirilmamis. firebase-config.js dosyasindaki degerleri doldurun.");
            return false;
        }
    } catch (e) {
        console.error("Firebase baglanti hatasi:", e);
        return false;
    }
    return false;
}
