import { Component } from "react";
/** Catches render errors so a bug never leaves the user on a blank white page. Resets when the route changes. */
export class ErrorBoundary extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("UI error", error, info.componentStack);
  }
  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="center-screen">
        <div className="card" style={{ maxWidth: 480, textAlign: "center" }}>
          <h1 className="title-lg">Something went wrong</h1>
          <p className="muted" style={{ margin: "12px 0 24px" }}>
            This screen hit an unexpected error. Your tickets and messages are saved.
          </p>
          <div className="row" style={{ justifyContent: "center" }}>
            <button className="btn btn-primary" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
            <button className="btn btn-secondary" onClick={() => window.location.reload()}>
              Reload page
            </button>
          </div>
          <p className="caption mono" style={{ marginTop: 20 }}>
            {this.state.error.message}
          </p>
        </div>
      </div>
    );
  }
}
