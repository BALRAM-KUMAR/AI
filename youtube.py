youtube.py
from typing import Dict, List, Optional, Any
import json
import os
from datetime import datetime, timedelta
import httpx
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.models.enums import AccountStatus, InfluencerCategory, ContentType
from app.models.social import InfluencerSocialAccount, SocialMediaPlatform
from app.repositories.social.influencer_social_account_repository import InfluencerSocialAccountRepository
from app.repositories.social.social_media_platform_repository import SocialMediaPlatformRepository
from app.core.config import settings
from pathlib import Path

class YouTubeService:
    """Service for handling YouTube API integration and data retrieval."""
    
    # YouTube API scopes needed for our application
    SCOPES = [
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email"
    ]
    
    # YouTube API service name and version
    API_SERVICE_NAME = "youtube"
    API_VERSION = "v3"
    
    def __init__(
        self, 
        social_account_repo: InfluencerSocialAccountRepository,
        platform_repo: SocialMediaPlatformRepository
    ):
        self.social_account_repo = social_account_repo
        self.platform_repo = platform_repo
        self.config = self._load_youtube_config()
        
    def _load_youtube_config(self) -> Dict:
        try:
            current_file = Path(__file__)
            project_root = current_file.parent.parent.parent
            config_path = project_root  / "core" / "social_config" / "you_tube.json"
            print(f"Looking for config at: {config_path}")
            with open(config_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"YouTube config not found at: {config_path}\n"
                f"Current working dir: {os.getcwd()}\n"
                f"Make sure the file exists and the path is correct."
            )
    
    def get_authorization_url(self, redirect_uri: str = None) -> Dict[str, str]:
        """Generate authorization URL for YouTube OAuth."""
        client_config = self.config["client_config"]
        
        # Validate redirect_uri against allowed URIs
        if redirect_uri and redirect_uri not in client_config["redirect_uris"]:
            raise ValueError("Invalid redirect_uri")
            
        if redirect_uri:
            flow_redirect_uri = redirect_uri
        else:
            flow_redirect_uri = client_config["redirect_uris"][0]
        
        flow = Flow.from_client_config(
            {"web": client_config},
            scopes=self.SCOPES
        )
        flow.redirect_uri = flow_redirect_uri
        
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent"
        )
        
        return {"authorization_url": auth_url, "state": state}
    
    async def handle_oauth_callback(
        self, 
        influencer_id: str, 
        auth_code: str, 
        redirect_uri: str = None
    ) -> InfluencerSocialAccount:
        """Process OAuth callback and save account token."""
        try:
            # Use the provided redirect_uri or default to the first one in config
            client_config = self.config["client_config"]
            if redirect_uri:
                flow_redirect_uri = redirect_uri
            else:
                flow_redirect_uri = client_config["redirect_uris"][0]
                
            # Create a direct request to the token endpoint instead of using Flow.fetch_token
            # This gives us more control over the token exchange process
            token_url = "https://oauth2.googleapis.com/token"
            token_data = {
                "code": auth_code,
                "client_id": client_config["client_id"],
                "client_secret": client_config["client_secret"],
                "redirect_uri": flow_redirect_uri,
                "grant_type": "authorization_code"
            }
            
            # Make the token request
            response = httpx.post(token_url, data=token_data)
            response.raise_for_status()  # Raise exception for HTTP errors
            
            # Parse token response
            token_json = response.json()
            if 'access_token' not in token_json:
                raise ValueError(f"No access token in response: {token_json}")
                
            # Create credentials object from the token response
            credentials = Credentials(
                token=token_json["access_token"],
                refresh_token=token_json.get("refresh_token"),  # May be None for non-offline access
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_config["client_id"],
                client_secret=client_config["client_secret"],
                scopes=token_json.get("scope", "").split(" "),
                expiry=datetime.utcnow() + timedelta(seconds=token_json["expires_in"])
            )
            
            # Get platform ID for YouTube
            platform = await self.platform_repo.get_by_name("YouTube")
            if not platform:
                raise ValueError("YouTube platform not found in database")
            
            # Get channel info to retrieve username
            youtube = build(
                self.API_SERVICE_NAME,
                self.API_VERSION,
                credentials=credentials
            )
            
            channel_response = youtube.channels().list(
                part="snippet,statistics,contentDetails",
                mine=True
            ).execute()
            
            if not channel_response["items"]:
                raise ValueError("No YouTube channel found for this account")
            
            channel_data = channel_response["items"][0]
            username = channel_data["snippet"]["title"]
            
            # Check if account already exists
            existing_account = await self.social_account_repo.get_by_platform_and_username(
                platform_id=platform.id,
                username=username
            )
            
            token_data = {
                "token": credentials.token,
                "refresh_token": credentials.refresh_token,
                "token_uri": credentials.token_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "scopes": credentials.scopes,
                "expiry": credentials.expiry.isoformat() if credentials.expiry else None
            }
            
            # Determine channel category based on keywords
            category = self._determine_channel_category(channel_data)
            
            # Prepare platform data
            platform_data = {
                "channel_id": channel_data["id"],
                "title": channel_data["snippet"]["title"],
                "description": channel_data["snippet"]["description"],
                "custom_url": channel_data["snippet"].get("customUrl"),
                "published_at": channel_data["snippet"]["publishedAt"],
                "thumbnail_url": channel_data["snippet"]["thumbnails"]["high"]["url"],
                "view_count": int(channel_data["statistics"].get("viewCount", 0)),
                "subscriber_count": int(channel_data["statistics"].get("subscriberCount", 0)),
                "video_count": int(channel_data["statistics"].get("videoCount", 0)),
                "country": channel_data["snippet"].get("country")
            }
            
            # Calculate engagement rate (simplistic version)
            total_views = int(channel_data["statistics"].get("viewCount", 0))
            subscribers = int(channel_data["statistics"].get("subscriberCount", 0))
            videos = int(channel_data["statistics"].get("videoCount", 0))
            
            engagement_rate = 0
            if subscribers > 0 and videos > 0:
                # Simple engagement calculation: (views / (subscribers * videos)) * 100
                engagement_rate = round((total_views / (subscribers * videos)) * 100, 2)
                # Cap at 100%
                engagement_rate = min(engagement_rate, 100)
            
            if existing_account:
                # Update existing account
                update_data = {
                    "token": json.dumps(token_data),
                    "account_status": AccountStatus.CONNECTED,
                    "platform_data": platform_data,
                    "followers_count": subscribers,
                    "engagement_rate": engagement_rate,
                    "category": category,
                    "last_synced": datetime.utcnow()
                }
                
                session = self.social_account_repo.session
                updated_account = await self.social_account_repo.update(
                    db=session,
                    db_obj=existing_account,
                    obj_in=update_data
                )
                return updated_account
            
            # Create new account
            new_account_data = {
                "influencer_id": influencer_id,
                "platform_id": platform.id,
                "username": username,
                "category": category,
                "platform_data": platform_data,
                "followers_count": subscribers,
                "engagement_rate": engagement_rate,
                "verified": "verified" in platform_data.get("title", "").lower(),  # Basic verification check
                "token": json.dumps(token_data),
                "account_status": AccountStatus.CONNECTED,
                "last_synced": datetime.utcnow()
            }
            
            session = self.social_account_repo.session
            new_account = await self.social_account_repo.create(
                db=session,
                obj_in=new_account_data
            )
            
            return new_account
            
        except Exception as e:
            # Provide detailed error information
            print(f"OAuth callback handling error: {str(e)}")
            import traceback
            traceback.print_exc()
            raise ValueError(f"OAuth token exchange failed: {str(e)}")
    
    def _determine_channel_category(self, channel_data: Dict) -> InfluencerCategory:
        """Determine channel category based on title, description, and other metadata."""
        title = channel_data["snippet"]["title"].lower()
        description = channel_data["snippet"].get("description", "").lower()
        
        # Combine all text for keyword matching
        all_text = f"{title} {description}"
        
        # Define category keywords
        category_keywords = {
            InfluencerCategory.BEAUTY: ["beauty", "makeup", "skincare", "cosmetics"],
            InfluencerCategory.FASHION: ["fashion", "style", "clothing", "outfit"],
            InfluencerCategory.FITNESS: ["fitness", "workout", "gym", "exercise", "health"],
            InfluencerCategory.FOOD: ["food", "cooking", "recipe", "chef", "cuisine"],
            InfluencerCategory.GAMING: ["gaming", "game", "playthrough", "streamer"],
            InfluencerCategory.TRAVEL: ["travel", "adventure", "destination", "tourism"],
            InfluencerCategory.TECH: ["tech", "technology", "gadget", "review", "unboxing"],
            InfluencerCategory.EDUCATION: ["education", "tutorial", "learn", "course"],
            InfluencerCategory.ENTERTAINMENT: ["entertainment", "comedy", "funny", "prank"],
            InfluencerCategory.BUSINESS: ["business", "entrepreneur", "startup", "finance"],
        }
        
        # Find category with most keyword matches
        best_category = InfluencerCategory.LIFESTYLE  # Default
        max_matches = 0
        
        for category, keywords in category_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in all_text)
            if matches > max_matches:
                max_matches = matches
                best_category = category
                
        return best_category
    
    async def refresh_account_data(self, account_id: str) -> InfluencerSocialAccount:
        """Refresh YouTube account data using stored credentials."""
        session = self.social_account_repo.session
        account = await self.social_account_repo.get(db=session, id=account_id)
        
        if not account or account.account_status != AccountStatus.CONNECTED:
            raise ValueError("Account not found or not connected")
        
        try:
            token_data = json.loads(account.token)
            credentials = Credentials(
                token=token_data["token"],
                refresh_token=token_data["refresh_token"],
                token_uri=token_data["token_uri"],
                client_id=token_data["client_id"],
                client_secret=token_data["client_secret"],
                scopes=token_data["scopes"]
            )
            
            # Check if token needs refresh
            if datetime.fromisoformat(token_data["expiry"]) < datetime.utcnow():
                # Token is expired, needs refresh
                credentials.refresh(httpx.Request())
                
                # Update token data
                token_data["token"] = credentials.token
                token_data["expiry"] = (datetime.utcnow() + timedelta(seconds=credentials.expires_in)).isoformat()
                
            # Fetch updated channel data
            youtube = build(
                self.API_SERVICE_NAME,
                self.API_VERSION,
                credentials=credentials
            )
            
            channel_response = youtube.channels().list(
                part="snippet,statistics,contentDetails",
                id=account.platform_data["channel_id"]
            ).execute()
            
            if not channel_response["items"]:
                raise ValueError("Channel no longer available")
                
            channel_data = channel_response["items"][0]
            
            # Prepare updated platform data
            platform_data = {
                "channel_id": channel_data["id"],
                "title": channel_data["snippet"]["title"],
                "description": channel_data["snippet"]["description"],
                "custom_url": channel_data["snippet"].get("customUrl"),
                "published_at": channel_data["snippet"]["publishedAt"],
                "thumbnail_url": channel_data["snippet"]["thumbnails"]["high"]["url"],
                "view_count": int(channel_data["statistics"].get("viewCount", 0)),
                "subscriber_count": int(channel_data["statistics"].get("subscriberCount", 0)),
                "video_count": int(channel_data["statistics"].get("videoCount", 0)),
                "country": channel_data["snippet"].get("country")
            }
            
            # Calculate engagement rate
            total_views = int(channel_data["statistics"].get("viewCount", 0))
            subscribers = int(channel_data["statistics"].get("subscriberCount", 0))
            videos = int(channel_data["statistics"].get("videoCount", 0))
            
            engagement_rate = 0
            if subscribers > 0 and videos > 0:
                engagement_rate = round((total_views / (subscribers * videos)) * 100, 2)
                engagement_rate = min(engagement_rate, 100)
            
            # Update account
            update_data = {
                "token": json.dumps(token_data),
                "platform_data": platform_data,
                "followers_count": subscribers,
                "engagement_rate": engagement_rate,
                "last_synced": datetime.utcnow()
            }
            
            updated_account = await self.social_account_repo.update(
                db=session,
                db_obj=account,
                obj_in=update_data
            )
            
            return updated_account
            
        except Exception as e:
            # If refresh fails, mark account as disconnected
            await self.social_account_repo.update(
                db=session,
                db_obj=account,
                obj_in={"account_status": AccountStatus.DISCONNECTED}
            )
            raise ValueError(f"Failed to refresh account: {str(e)}")
    
    async def get_recent_videos(self, account_id: str, max_results: int = 10) -> List[Dict]:
        """Get recent videos from a connected YouTube channel."""
        session = self.social_account_repo.session
        account = await self.social_account_repo.get(db=session, id=account_id)
        
        if not account or account.account_status != AccountStatus.CONNECTED:
            raise ValueError("Account not found or not connected")
        
        token_data = json.loads(account.token)
        credentials = Credentials(
            token=token_data["token"],
            refresh_token=token_data["refresh_token"],
            token_uri=token_data["token_uri"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            scopes=token_data["scopes"]
        )
        
        youtube = build(
            self.API_SERVICE_NAME,
            self.API_VERSION,
            credentials=credentials
        )
        
        # Get channel uploads playlist ID
        channel_response = youtube.channels().list(
            part="contentDetails",
            id=account.platform_data["channel_id"]
        ).execute()
        
        if not channel_response["items"]:
            raise ValueError("Channel not found")
            
        uploads_playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        
        # Get videos from uploads playlist
        videos_response = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=max_results
        ).execute()
        
        videos = []
        for item in videos_response.get("items", []):
            video_id = item["contentDetails"]["videoId"]
            
            # Get video statistics
            video_stats = youtube.videos().list(
                part="statistics",
                id=video_id
            ).execute()
            
            stats = video_stats["items"][0]["statistics"] if video_stats["items"] else {}
            
            videos.append({
                "video_id": video_id,
                "title": item["snippet"]["title"],
                "description": item["snippet"]["description"],
                "published_at": item["snippet"]["publishedAt"],
                "thumbnail_url": item["snippet"]["thumbnails"]["high"]["url"],
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
            })
            
        return videos
    
    async def get_channel_analytics(self, account_id: str) -> Dict[str, Any]:
        """Get analytics data for a YouTube channel."""
        session = self.social_account_repo.session
        account = await self.social_account_repo.get(db=session, id=account_id)
        
        if not account or account.account_status != AccountStatus.CONNECTED:
            raise ValueError("Account not found or not connected")
        
        # Get recent videos
        recent_videos = await self.get_recent_videos(account_id, max_results=50)
        
        # Calculate analytics
        total_views = sum(video["view_count"] for video in recent_videos)
        total_likes = sum(video["like_count"] for video in recent_videos)
        total_comments = sum(video["comment_count"] for video in recent_videos)
        
        avg_views = total_views / len(recent_videos) if recent_videos else 0
        avg_likes = total_likes / len(recent_videos) if recent_videos else 0
        avg_comments = total_comments / len(recent_videos) if recent_videos else 0
        
        # Calculate engagement rate for recent videos
        engagement_rate = 0
        if total_views > 0:
            engagement_rate = ((total_likes + total_comments) / total_views) * 100
            
        # Find top performing videos
        videos_by_views = sorted(recent_videos, key=lambda x: x["view_count"], reverse=True)
        videos_by_engagement = sorted(
            recent_videos, 
            key=lambda x: (x["like_count"] + x["comment_count"]) / max(x["view_count"], 1),
            reverse=True
        )
        
        return {
            "channel_stats": {
                "total_subscribers": account.followers_count,
                "total_videos": account.platform_data.get("video_count", 0),
                "total_views": account.platform_data.get("view_count", 0),
            },
            "recent_video_stats": {
                "videos_analyzed": len(recent_videos),
                "avg_views": avg_views,
                "avg_likes": avg_likes,
                "avg_comments": avg_comments,
                "engagement_rate": engagement_rate
            },
            "top_videos_by_views": videos_by_views[:5],
            "top_videos_by_engagement": videos_by_engagement[:5]
        }
    
    async def get_trending_videos(self, region_code: str = "US", category_id: str = None, max_results: int = 10) -> List[Dict]:
        """Get trending videos from YouTube."""
        youtube = build(
            self.API_SERVICE_NAME,
            self.API_VERSION,
            developerKey=self.config["api_key"]
        )
        
        # Build request parameters
        params = {
            "part": "snippet,contentDetails,statistics",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": max_results
        }
        
        if category_id:
            params["videoCategoryId"] = category_id
            
        # Get trending videos
        response = youtube.videos().list(**params).execute()
        
        trending_videos = []
        for item in response.get("items", []):
            trending_videos.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "channel_title": item["snippet"]["channelTitle"],
                "channel_id": item["snippet"]["channelId"],
                "published_at": item["snippet"]["publishedAt"],
                "thumbnail_url": item["snippet"]["thumbnails"]["high"]["url"],
                "view_count": int(item["statistics"].get("viewCount", 0)),
                "like_count": int(item["statistics"].get("likeCount", 0)),
                "comment_count": int(item["statistics"].get("commentCount", 0)),
                "tags": item["snippet"].get("tags", []),
                "duration": item["contentDetails"]["duration"]
            })
            
        return trending_videos
    
    async def get_video_categories(self, region_code: str = "US") -> List[Dict]:
        """Get available video categories for a region."""
        youtube = build(
            self.API_SERVICE_NAME,
            self.API_VERSION,
            developerKey=self.config["api_key"]
        )
        
        response = youtube.videoCategories().list(
            part="snippet",
            regionCode=region_code
        ).execute()
        
        categories = []
        for item in response.get("items", []):
            categories.append({
                "id": item["id"],
                "title": item["snippet"]["title"]
            })
            
        return categories
















from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.base import BaseRepository
from app.models.social import BrandProfile
from app.models.enums import CompanySize, CompanyType

class BrandProfileRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.model = BrandProfile

    async def get_by_brand_id(self, brand_id: str) -> Optional[BrandProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.brand_id == brand_id)
        )
        return result.scalar_one_or_none()

    async def get_by_company_name(self, company_name: str) -> Optional[BrandProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.company_name == company_name)
        )
        return result.scalar_one_or_none()

    async def get_by_company_size(self, company_size: CompanySize) -> List[BrandProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.company_size == company_size)
        )
        return result.scalars().all()

    async def get_by_company_type(self, company_type: CompanyType) -> List[BrandProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.company_type == company_type)
        )
        return result.scalars().all() 




from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.base import BaseRepository
from app.models.social import InfluencerProfile
from app.models.enums import InfluencerCategory, ContentType

class InfluencerProfileRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.model = InfluencerProfile

    async def get_by_influencer_id(self, influencer_id: str) -> Optional[InfluencerProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.influencer_id == influencer_id)
        )
        return result.scalar_one_or_none()

    async def get_by_category(self, category: InfluencerCategory) -> List[InfluencerProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.categories.contains([category]))
        )
        return result.scalars().all()

    async def get_by_content_type(self, content_type: ContentType) -> List[InfluencerProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.content_types.contains([content_type]))
        )
        return result.scalars().all()

    async def get_by_language(self, language: str) -> List[InfluencerProfile]:
        result = await self.session.execute(
            select(self.model).where(self.model.languages.contains([language]))
        )
        return result.scalars().all() 



from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.base import BaseRepository
from app.models.social import InfluencerSocialAccount
from app.models.enums import AccountStatus

class InfluencerSocialAccountRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.model = InfluencerSocialAccount

    async def get_by_username(self, username: str) -> Optional[InfluencerSocialAccount]:
        result = await self.session.execute(
            select(self.model).where(self.model.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_influencer_id(self, influencer_id: str) -> List[InfluencerSocialAccount]:
        result = await self.session.execute(
            select(self.model).where(self.model.influencer_id == influencer_id)
        )
        return result.scalars().all()

    async def get_by_platform_and_username(self, platform_id: str, username: str) -> Optional[InfluencerSocialAccount]:
        result = await self.session.execute(
            select(self.model).where(
                self.model.platform_id == platform_id,
                self.model.username == username
            )
        )
        return result.scalar_one_or_none()

    async def get_connected_accounts(self, influencer_id: str) -> List[InfluencerSocialAccount]:
        result = await self.session.execute(
            select(self.model).where(
                self.model.influencer_id == influencer_id,
                self.model.account_status == AccountStatus.CONNECTED
            )
        )
        return result.scalars().all() 


from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.base import BaseRepository
from app.models.social import SocialMediaPlatform

class SocialMediaPlatformRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.model = SocialMediaPlatform

    async def get_by_name(self, name: str) -> Optional[SocialMediaPlatform]:
        result = await self.session.execute(
            select(self.model).where(self.model.name == name)
        )
        return result.scalar_one_or_none()

    async def get_supported_platforms(self) -> List[SocialMediaPlatform]:
        result = await self.session.execute(
            select(self.model).where(self.model.is_supported == True)
        )
        return result.scalars().all() 

base.py

from typing import Generic, TypeVar, Type, Optional, List, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.base import Base

ModelType = TypeVar("ModelType")

class BaseRepository(Generic[ModelType]):
    def __init__(self, model: Type[ModelType]):
        self.model = model

    async def get(self, db: AsyncSession, id: Any) -> Optional[ModelType]:
        result = await db.execute(select(self.model).filter(self.model.id == id))
        return result.scalar_one_or_none()

    async def get_multi(
        self, db: AsyncSession, *, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        result = await db.execute(select(self.model).offset(skip).limit(limit))
        return result.scalars().all()

    async def create(self, db: AsyncSession, *, obj_in: Dict[str, Any]) -> ModelType:
        db_obj = self.model(**obj_in)
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def update(
        self, db: AsyncSession, *, db_obj: ModelType, obj_in: Dict[str, Any]
    ) -> ModelType:
        for field in obj_in:
            setattr(db_obj, field, obj_in[field])
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def remove(self, db: AsyncSession, *, id: Any) -> ModelType:
        result = await db.execute(select(self.model).filter(self.model.id == id))
        obj = result.scalar_one_or_none()
        if obj:
            await db.delete(obj)
            await db.commit()
        return obj

    async def get_by_field(
        self, db: AsyncSession, *, field: str, value: Any
    ) -> Optional[ModelType]:
        result = await db.execute(
            select(self.model).filter(getattr(self.model, field) == value)
        )
        return result.scalar_one_or_none()

    async def get_multi_by_field(
        self, db: AsyncSession, *, field: str, value: Any, skip: int = 0, limit: int = 100
    ) -> List[ModelType]:
        result = await db.execute(
            select(self.model)
            .filter(getattr(self.model, field) == value)
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all() 




