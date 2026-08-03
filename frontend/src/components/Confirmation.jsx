import React, { useState, useEffect } from "react";
import { useAuth0 } from "@auth0/auth0-react";

const getCsrfToken = () => {
  return document.cookie
    .split("; ")
    .find((row) => row.startsWith("csrftoken="))
    ?.split("=")[1];
};

const airportNames = {
  "YVR": "Vancouver Int'l Airport",
  "NRT": "Tokyo Narita Airport",
  "HRE": "Harare Int'l Airport",
  "LAX": "Los Angeles Int'l Airport",
  "MAD": "Madrid Barajas Airport",
  "JFK": "John F. Kennedy Int'l Airport"
};

export default function Confirmation({ resourceUri, accessToken, onBack }) {
  const { user, logout } = useAuth0();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [cheqPayload, setCheqPayload] = useState(null);
  const [performUri, setPerformUri] = useState(null);
  const [flight, setFlight] = useState(null);
  
  // User Dropdown State
  const [showUserDropdown, setShowUserDropdown] = useState(false);

  // Secure user inputs (Zero-LLM direct collection)
  const [passengerName, setPassengerName] = useState("");
  const [passportNumber, setPassportNumber] = useState("");
  const [cardNumber, setCardNumber] = useState("");
  const [cardExpiry, setCardExpiry] = useState("");
  const [cardCvv, setCardCvv] = useState("");
  
  const [consentChecked, setConsentChecked] = useState(false);
  const [isFareExpanded, setIsFareExpanded] = useState(false);
  const [showStopoverPopup, setShowStopoverPopup] = useState(false);
  const [actionLoading, setActionLoading] = useState(null); // 'accept' or 'reject'
  const [completionState, setCompletionState] = useState(null); // 'success' or 'rejected'

  useEffect(() => {
    fetchCheqDetails();
  }, [resourceUri]);

  const fetchCheqDetails = async () => {
    setLoading(true);
    setError(null);
    try {
      const headers = {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      };
      if (accessToken) {
        headers["Authorization"] = `Bearer ${accessToken}`;
      }

      const response = await fetch("http://127.0.0.1:8000/confirmation_server/trigger_confirmation/", {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ resource_uri: resourceUri }),
      });

      if (!response.ok) {
        throw new Error(`Flight booking details could not be found or has expired (${response.status})`);
      }

      const data = await response.json();
      setCheqPayload(data.CHEQ);
      setPerformUri(data.perform_confirmation_uri);

      // Extract flight information from the inputs parameters
      const parameters = data.CHEQ?.inputs?.parameters || [];
      const resourceWithFlight = parameters.find(p => p.selected_flight);
      
      if (resourceWithFlight && resourceWithFlight.selected_flight) {
        setFlight(resourceWithFlight.selected_flight);
      } else {
        setError("Flight selection details could not be found in the confirmation payload. Please select a flight first.");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleDecision = async (decision) => {
    if (decision === "ACCEPT" && !consentChecked) return;
    setActionLoading(decision.toLowerCase());
    
    try {
      const bodyPayload = {
        resource_uri: resourceUri,
        decision: decision,
      };

      // Add secure inputs if accepted ( Zero-LLM direct collection )
      if (decision === "ACCEPT") {
        bodyPayload.passenger_name = passengerName;
        bodyPayload.passport_number = passportNumber;
        bodyPayload.card_number = cardNumber;
        bodyPayload.card_expiry = cardExpiry;
        bodyPayload.card_cvv = cardCvv;
      }

      const headers = {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      };
      if (accessToken) {
        headers["Authorization"] = `Bearer ${accessToken}`;
      }

      const response = await fetch(performUri || "http://127.0.0.1:8000/confirmation_server/perform_confirmation", {
        method: "POST",
        headers: headers,
        body: JSON.stringify(bodyPayload),
      });

      if (response.ok) {
        setCompletionState(decision === "ACCEPT" ? "success" : "rejected");
      } else {
        const errText = await response.text();
        alert(`Error submitting decision: ${errText}`);
      }
    } catch (e) {
      alert(`Network error: ${e.message}`);
    } finally {
      setActionLoading(null);
    }
  };

  // Calculate taxes & total dynamically
  const baseFare = flight ? Math.round(flight.price * 0.9) : 0;
  const taxesFees = flight ? Math.round(flight.price * 0.1) : 0;
  const totalCost = flight ? flight.price : 0;
  const budgetLimit = 1500;
  const priceExceeded = totalCost > budgetLimit;
  const priceExceededAmount = Math.round(totalCost - budgetLimit);

  if (loading) {
    return (
      <div className="conf-container">
        <div className="conf-card shimmer-card">
          <div className="shimmer-header" />
          <div className="shimmer-banner" />
          <div className="shimmer-body" />
          <div className="shimmer-body" style={{ width: "80%" }} />
          <div className="shimmer-button-row" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="conf-container">
        <div className="conf-card error-card">
          <div className="error-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <h2>Flight Booking Not Found</h2>
          <p className="error-desc">{error}</p>
          <button className="back-btn" onClick={onBack}>Go back to chat</button>
        </div>
      </div>
    );
  }

  if (completionState === "success") {
    return (
      <div className="conf-container">
        <div className="conf-card success-card">
          <div className="success-checkmark-wrapper">
            <div className="success-checkmark" />
          </div>
          <h2>Booking Authorized</h2>
          <p className="success-desc">The cryptographic decision signature has been successfully transmitted. Your agent will be notified immediately.</p>
          <div className="confetti-dots">
            <span className="dot dot-1" /><span className="dot dot-2" /><span className="dot dot-3" />
          </div>
          <button className="back-btn success-btn" onClick={() => onBack("ACCEPT")}>Return to Chat</button>
        </div>
      </div>
    );
  }

  if (completionState === "rejected") {
    return (
      <div className="conf-container">
        <div className="conf-card rejected-card">
          <div className="reject-cross-wrapper">
            <div className="reject-cross" />
          </div>
          <h2>Request Denied</h2>
          <p className="reject-desc">The proposed action has been rejected and cancelled. The agent will adjust the search constraints.</p>
          <button className="back-btn reject-btn" onClick={() => onBack("REJECT")}>Return to Chat</button>
        </div>
      </div>
    );
  }

  return (
    <div className="conf-container">
      {/* Session/Login Bar with Clickable Profile Dropdown */}
      <header className="conf-top-bar">
        <div className="bar-title">
          <span className="secure-badge">🛡️ Secure checkout</span>
        </div>
        
        <div className="profile-dropdown-container">
          <div 
            className="profile-avatar-trigger"
            onClick={() => setShowUserDropdown(!showUserDropdown)}
          >
            {user?.picture ? (
              <img src={user.picture} alt={user?.name || "User"} className="profile-avatar-img" />
            ) : (
              user?.name ? user.name[0].toUpperCase() : "U"
            )}
          </div>
          
          {showUserDropdown && (
            <div className="dropdown-menu">
              <div className="dropdown-user-details">
                <span className="dropdown-user-name">{user?.name || "User Account"}</span>
                <span className="dropdown-user-email">{user?.email || "session_active"}</span>
              </div>
              <button 
                className="dropdown-btn"
                onClick={() => {
                  setShowUserDropdown(false);
                  logout({ logoutParams: { returnTo: window.location.origin } });
                }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
                </svg>
                Sign Out
              </button>
            </div>
          )}
        </div>
      </header>

      <main className="conf-main-content">
        <div className="conf-card main-flight-card">
          {/* Cryptographic Badge */}
          <div className="crypto-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            <span>Verified CHEQ Signature</span>
          </div>

          {/* Dribbble Style Flight Header */}
          <div className="flight-card-header">
            <div className="airline-brand">
              <span className="carrier-logo">{flight?.airline ? flight.airline[0] : "✈"}</span>
              <div>
                <h3 className="airline-name">{flight?.airline}</h3>
                <p className="aircraft-model">{flight?.airplane}</p>
              </div>
            </div>
            <span className="flight-badge">{flight?.flight_number}</span>
          </div>

          {/* Dribbble Style Airport Code Timeline */}
          <div className="flight-route-section">
            <div className="route-stop">
              <h2 className="airport-code">{flight?.origin}</h2>
              <p className="airport-label">{airportNames[flight?.origin] || "Departure Airport"}</p>
              <h4 className="flight-time">{flight?.departure_time ? flight.departure_time.substring(0, 5) : ""}</h4>
            </div>

            <div className="timeline-connector">
              <span className="duration-text">
                {flight ? `${Math.floor(flight.duration_minutes / 60)}h ${flight.duration_minutes % 60}m` : ""}
              </span>
              <div className="timeline-line">
                {flight?.stops > 0 && (
                  <div 
                    className="stopover-dot" 
                    onMouseEnter={() => setShowStopoverPopup(true)}
                    onMouseLeave={() => setShowStopoverPopup(false)}
                  >
                    {showStopoverPopup && (
                      <div className="stopover-popup">
                        <div style={{ fontWeight: 600, color: '#f3f4f6', marginBottom: '2px' }}>
                          1 Layover Connection
                        </div>
                        <div style={{ color: '#d1d5db', fontSize: '10px' }}>
                          <strong>Transfer:</strong> {flight?.origin === 'YVR' && flight?.destination === 'NRT' ? 'Tokyo Narita (NRT)' : 'Hub Airport'}
                        </div>
                        <div style={{ color: '#9ca3af', fontSize: '9.5px', marginTop: '2px' }}>
                          ⏱️ Layover duration: 2h 15m
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
              <p className="stops-count">{flight?.stops === 0 ? "Direct" : `${flight.stops} Stop`}</p>
            </div>

            <div className="route-stop text-right">
              <h2 className="airport-code">{flight?.destination}</h2>
              <p className="airport-label">{airportNames[flight?.destination] || "Arrival Airport"}</p>
              <h4 className="flight-time">{flight?.arrival_time ? flight.arrival_time.substring(0, 5) : ""}</h4>
            </div>
          </div>

          {/* Price & Budget Deviation Badge */}
          <div className="price-tag-row">
            <div className="price-label">
              <span>Proposed Price</span>
              <h2 className="total-price">${flight?.price} CAD</h2>
            </div>
            {priceExceeded && (
              <div className="budget-warning">
                <span className="warning-icon">⚠️</span>
                <span>Exceeds budget constraint by ${priceExceededAmount}</span>
              </div>
            )}
          </div>

          {/* Collapsible Fare Details */}
          <div className="fare-collapsible">
            <button 
              className="fare-toggle-btn"
              onClick={() => setIsFareExpanded(!isFareExpanded)}
            >
              <span>{isFareExpanded ? "Hide Fare Details" : "Show Fare Details"}</span>
              <svg 
                className={`arrow-icon ${isFareExpanded ? "arrow-up" : ""}`} 
                width="16" 
                height="16" 
                viewBox="0 0 24 24" 
                fill="none" 
                stroke="currentColor" 
                strokeWidth="2"
              >
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            
            {isFareExpanded && (
              <div className="fare-content-box">
                <div className="fare-row">
                  <span>Base Flight Fare</span>
                  <span>${baseFare}.00 CAD</span>
                </div>
                <div className="fare-row">
                  <span>Airport Taxes & Fuel Surcharge</span>
                  <span>${taxesFees}.00 CAD</span>
                </div>
                <hr className="fare-divider" />
                <div className="fare-row bold-fare">
                  <span>Total Amount Charged</span>
                  <span>${totalCost}.00 CAD</span>
                </div>
                <div className="baggage-allowance">
                  <div className="baggage-badge">👜 Cabin bag included</div>
                  <div className="baggage-badge">🧳 1x Checked bag (23kg)</div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Secure human inputs section */}
        <div className="conf-card secure-inputs-card">
          <div className="card-header-icon">
            <span className="shield-icon">🛡️</span>
            <div>
              <h3>Secure Passenger Details</h3>
              <p className="card-subtitle">Encrypted Zero-LLM direct input (hidden from AI agent)</p>
            </div>
          </div>
          
          <div className="form-group">
            <label className="form-label" htmlFor="passName">Passenger Full Name (as in Passport)</label>
            <input 
              id="passName"
              type="text" 
              className="form-input" 
              placeholder="e.g. John Doe"
              value={passengerName}
              onChange={(e) => setPassengerName(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="passNum">Passport Number</label>
            <input 
              id="passNum"
              type="text" 
              className="form-input" 
              placeholder="e.g. AA1234567"
              value={passportNumber}
              onChange={(e) => setPassportNumber(e.target.value)}
            />
          </div>

          <div className="form-row-grid">
            <div className="form-group">
              <label className="form-label" htmlFor="cardNum">Credit Card Number</label>
              <input 
                id="cardNum"
                type="text" 
                className="form-input" 
                placeholder="4111 2222 3333 4444"
                value={cardNumber}
                onChange={(e) => setCardNumber(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="expiry">Expiry (MM/YY)</label>
              <input 
                id="expiry"
                type="text" 
                className="form-input" 
                placeholder="12/28"
                style={{ textAlign: "center" }}
                value={cardExpiry}
                onChange={(e) => setCardExpiry(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="cvv">CVV</label>
              <input 
                id="cvv"
                type="password" 
                className="form-input" 
                placeholder="***"
                maxLength={4}
                style={{ textAlign: "center" }}
                value={cardCvv}
                onChange={(e) => setCardCvv(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Consent Checkbox */}
        <div className="consent-agreement">
          <label className="checkbox-container">
            <input 
              type="checkbox" 
              checked={consentChecked}
              onChange={(e) => setConsentChecked(e.target.checked)}
            />
            <span className="checkmark-box" />
            <span className="consent-text">
              I authorize the booking of this flight under the passenger details provided above and agree to the fare terms.
            </span>
          </label>
        </div>

        {/* Action Buttons */}
        <div className="button-group">
          <button 
            className="btn btn-reject"
            onClick={() => handleDecision("REJECT")}
            disabled={actionLoading !== null}
          >
            {actionLoading === "reject" ? "Rejecting..." : "Reject Booking"}
          </button>
          <button 
            className="btn btn-accept"
            onClick={() => handleDecision("ACCEPT")}
            disabled={!consentChecked || actionLoading !== null}
          >
            {actionLoading === "accept" ? "Signing Payload..." : "Accept & Authorize"}
          </button>
        </div>
      </main>
    </div>
  );
}
