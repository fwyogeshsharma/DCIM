import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root'
})
export class CoolingApiService {
  private apiUrl = environment.apiUrl + '/api/v1/cooling-metrics';

  constructor(private http: HttpClient) {}

  /**
   * Send cooling metrics to the server
   *
   * NOTE: Client certificate authentication (mTLS) in browsers:
   * - Browsers handle client certificates at the TLS handshake level
   * - Import certificates into browser's certificate store
   * - Browser will automatically present the cert when server requests it
   * - JavaScript cannot directly access .crt/.key files for security reasons
   *
   * For Development:
   * 1. Import client.crt into your browser's certificate store
   * 2. Browser will prompt when the server requests client certificate
   *
   * For Production:
   * - Use a backend proxy (Node.js/Express) that handles mTLS
   * - Angular app calls proxy, proxy calls the actual API with certificates
   */
  sendMetrics(data: any): Observable<any> {
    const headers = new HttpHeaders({
      'Content-Type': 'application/json'
    });

    return this.http.post(this.apiUrl, data, {
      headers,
      withCredentials: true  // Include credentials/certificates in request
    });
  }

  /**
   * Test API connection
   */
  testConnection(): Observable<any> {
    return this.http.get(this.apiUrl, {
      withCredentials: true
    });
  }
}
